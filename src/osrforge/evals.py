"""Eval scoring: truth-file models, alignment, and the pinned metric families.

This is the pure half of the eval harness — extraction changes are measured,
not vibed: deterministic, CI-tested code that scores a
workdir's stage caches against verified structural ground truth. The
live-network driver (`tools/eval/run_eval.py`) is repo-only wiring; everything
with behavior worth testing lives here. The scorer reads the stage caches —
never `adventure.json` — because evals measure *extraction*, and assembly's
best-effort fallbacks exist to mask extraction gaps in the playable draft,
which is exactly what a measurement must not let them do.

Truth files are structural-only (printed keys, names, and codes — no prose)
and are authored from the printed module under the independence discipline
(`tools/eval/AUTHORING.md`) — never from pipeline output; see
`tools/eval/README.md` for the corpus rules and the authoring conventions.
"""

import hashlib
import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from osrforge.assemble import parse_treasure, usable_stat_block
from osrforge.contracts.stages import (
    AreaContent,
    LevelContent,
    MapLevelReading,
    MapReading,
    MonsterResolutions,
    StatBlocks,
    SurveyDungeon,
    SurveyIndex,
)
from osrforge.geometry import transition_via
from osrforge.monsters import normalize_monster_name
from osrforge.reconcile import ProseEdge, merge_level_edges, select_entrance
from osrforge.settings import ConversionSettings
from osrforge.survey import canonical_slug
from osrforge.versioning import SCHEMA_VERSION
from osrforge.workdir import Workdir, write_json_artifact

__all__ = [
    "AreaMetrics",
    "ByomEntry",
    "ByomScoreboard",
    "ConnectionMetrics",
    "CorpusManifest",
    "DoorMetrics",
    "EncounterMetrics",
    "EntranceMetrics",
    "ManifestLicense",
    "ModuleMetrics",
    "ModuleScore",
    "ModuleTruth",
    "RunInfo",
    "Scoreboard",
    "TransitionMetrics",
    "TreasureMetrics",
    "TruthArea",
    "TruthDoor",
    "TruthDungeon",
    "TruthEncounter",
    "TruthEntrance",
    "TruthLevel",
    "TruthProvenance",
    "TruthTransition",
    "TruthTreasure",
    "corpus_means",
    "enforce_source_integrity",
    "load_byom_scoreboard",
    "load_manifest",
    "load_scoreboard",
    "load_truth",
    "publish_module",
    "rescore_modules",
    "save_byom_scoreboard",
    "save_scoreboard",
    "score_workdir",
    "settings_overrides",
    "sidecar_path",
    "verify_source",
]


class TruthEncounter(BaseModel):
    """One printed encounter: the creature name as the module's key prints it.

    `template` is the osrlib catalog id the name *should* resolve to, omitted
    when the module's monster genuinely has no SRD template (rank variants
    with their own stat blocks, module-specific creatures). `custom` is legal
    only with `template` omitted: it asserts *this creature should emit* — the
    printed page carries a usable stat block (an AC plus an HD line or a
    class-level notation, exactly assembly's refusal-ladder predicate).
    Omitted-with-`custom` moves the encounter into the custom metric pair;
    omitted-without stays `non_srd` — no SRD template and no assertion about
    emission. `count` is omitted when the module states none or a variable
    one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    template: str | None = None
    custom: bool = False
    count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _custom_only_without_template(self) -> TruthEncounter:
        if self.custom and self.template is not None:
            raise ValueError(f"{self.name!r}: custom asserts emission and is legal only when template is omitted")
        return self


class TruthTreasure(BaseModel):
    """Whether the printed area contains treasure, and its stated letter codes.

    `present` is true when the entry states coins, valuables, or magic items
    in the area (carried by its occupants included; rewards promised
    elsewhere excluded). `letters` only when the module states treasure-type
    letter codes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    present: bool
    letters: tuple[str, ...] = ()

    @field_validator("letters")
    @classmethod
    def _single_letters(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for letter in value:
            if len(letter) != 1 or not letter.isalpha() or letter.upper() != letter:
                raise ValueError(f"treasure letters are single uppercase codes: {letter!r}")
        return value


class TruthDoor(BaseModel):
    """A door fact asserted on one of an area's connections.

    `kind` mirrors the extraction contract's door vocabulary (`door` /
    `secret_door`); `locked` is asserted alongside it. Stuck state is
    deliberately not asserted — the corpus carries almost no printed signal
    for it, so a stuck metric would run on an empty denominator.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["door", "secret_door"]
    locked: bool = False


class TruthArea(BaseModel):
    """One keyed area, identified by its printed key.

    `encounters`, `connections`, `doors`, and `treasure` are assertion-aware:
    `None` (omitted) means the fact was not asserted — the area is out of the
    corresponding metric's universe. A present value asserts the complete
    fact set: the area's full encounter list (possibly empty — the
    asserted-empty convention the encounter-precision metric rides), its full
    same-level connected printed-key list (possibly empty), the complete
    door-fact set over its asserted connections, or its treasure facts.
    Assertion-awareness is what makes time-boxed partial truth honest: a truth
    file covering every area key plus a verified sample of areas still yields
    exact area recall and honestly-denominated agreement everywhere else.

    `doors` is keyed by neighbor printed key; every key must appear in the
    same area's `connections` (slug-matched), and asserting `doors` requires
    asserting `connections` — a door fact on an unasserted edge set would
    have no universe to score in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    encounters: tuple[TruthEncounter, ...] | None = None
    connections: tuple[str, ...] | None = None
    doors: dict[str, TruthDoor] | None = None
    treasure: TruthTreasure | None = None

    @model_validator(mode="after")
    def _doors_ride_asserted_connections(self) -> TruthArea:
        if self.doors is None:
            return self
        if self.connections is None:
            raise ValueError(f"{self.key!r}: doors asserted without connections — assert the edge set first")
        connection_slugs = {canonical_slug(neighbor) for neighbor in self.connections}
        for neighbor in self.doors:
            if canonical_slug(neighbor) not in connection_slugs:
                raise ValueError(f"{self.key!r}: doors names {neighbor!r}, which is not among its connections")
        return self


class TruthLevel(BaseModel):
    """One printed level.

    Area keys must be unique per level under `canonical_slug` (empty slugs
    are exempt — they take distinct positional fallbacks): the scorer matches
    areas by slug, and a duplicate would silently attribute the second area's
    facts to the first.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    areas: tuple[TruthArea, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _key_slugs_unique(self) -> TruthLevel:
        slugs = [slug for area in self.areas if (slug := canonical_slug(area.key))]
        if len(set(slugs)) != len(slugs):
            raise ValueError(f"truth area keys must be unique per level under canonical_slug: {slugs}")
        return self

    @model_validator(mode="after")
    def _door_assertions_agree(self) -> TruthLevel:
        implications: dict[frozenset[str], tuple[str, TruthDoor | None]] = {}
        asserting = [area for area in self.areas if area.doors is not None]
        asserting_slugs = {canonical_slug(area.key) for area in asserting}
        for area in asserting:
            area_slug = canonical_slug(area.key)
            for neighbor in area.connections or ():
                neighbor_slug = canonical_slug(neighbor)
                if neighbor_slug not in asserting_slugs:
                    continue
                pair = frozenset({area_slug, neighbor_slug})
                door = next(
                    (fact for key, fact in (area.doors or {}).items() if canonical_slug(key) == neighbor_slug),
                    None,
                )
                if pair in implications:
                    other_key, other_door = implications[pair]
                    if other_door != door:
                        raise ValueError(
                            f"contradictory door assertions on the pair {area.key!r}/{other_key}: "
                            f"{door!r} vs {other_door!r} — both endpoints assert doors and must agree, "
                            "including omission (an omitted neighbor is an explicit no-door)"
                        )
                else:
                    implications[pair] = (area.key, door)
        return self


class TruthTransition(BaseModel):
    """One vertical link between two levels of a dungeon, asserted once.

    Endpoint order is free — matching is undirected — and `kind` uses
    geometry's own narrowing (`trapdoor` and `chute` as themselves,
    everything else `stairs`). The travel sense is deliberately not asserted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_level: int = Field(ge=1)
    from_key: str
    to_level: int = Field(ge=1)
    to_key: str
    kind: Literal["stairs", "trapdoor", "chute"]

    @model_validator(mode="after")
    def _levels_differ(self) -> TruthTransition:
        if self.from_level == self.to_level:
            raise ValueError(f"a transition links two levels; got {self.from_level} on both ends")
        return self


class TruthEntrance(BaseModel):
    """The printed key of the area holding a dungeon's entrance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    level: int = Field(ge=1)
    key: str


class TruthDungeon(BaseModel):
    """One printed adventuring site.

    `transitions` and `entrance` are assertion-aware, dungeon-scoped facts:
    omitted asserts nothing; a present `transitions` asserts the dungeon's
    complete vertical-link set (levels are peers, so an edge between them
    belongs to the dungeon, not to either level), and a present `entrance`
    asserts which printed area holds the way in.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    levels: tuple[TruthLevel, ...] = Field(min_length=1)
    transitions: tuple[TruthTransition, ...] | None = None
    entrance: TruthEntrance | None = None

    @model_validator(mode="after")
    def _level_numbers_unique(self) -> TruthDungeon:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"truth level numbers must be unique per dungeon: {numbers}")
        return self

    @model_validator(mode="after")
    def _endpoints_exist(self) -> TruthDungeon:
        by_number = {level.number: level for level in self.levels}

        def check(level_number: int, key: str, label: str) -> None:
            level = by_number.get(level_number)
            if level is None:
                raise ValueError(f"{label} names level {level_number}, which this dungeon does not have")
            key_slug = canonical_slug(key)
            if not any(canonical_slug(area.key) == key_slug for area in level.areas):
                raise ValueError(f"{label} names key {key!r}, which level {level_number} does not have")

        for transition in self.transitions or ():
            check(transition.from_level, transition.from_key, f"transition in {self.name!r}")
            check(transition.to_level, transition.to_key, f"transition in {self.name!r}")
        if self.entrance is not None:
            check(self.entrance.level, self.entrance.key, f"entrance of {self.name!r}")
        return self


class ModuleTruth(BaseModel):
    """A corpus module's verified structural ground truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeons: tuple[TruthDungeon, ...] = Field(min_length=1)


class ManifestLicense(BaseModel):
    """The license record: SPDX id plus the note recording how the license was verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spdx: str
    verified: str


class TruthProvenance(BaseModel):
    """How the module's truth file came to be trusted.

    The record `publish` requires before a module's numbers reach the
    committed BYOM board: unverified truth can be scored locally all day, but
    it cannot put numbers on the committed record. `instrument` is free text
    by design — the cross-instrument rule (`tools/eval/AUTHORING.md`) is a
    stated preference, not a gate, because enforcement is impossible and
    false assurance is worse than none.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authored: str
    """The authoring date."""

    instrument: str
    """The authoring model or agent."""

    verified: str
    """The verification record: which legs (adversarial pass, owner sampling, CI baselines) actually ran."""


class CorpusManifest(BaseModel):
    """A corpus member's manifest — the whole redistribution surface.

    The corpus ships pointers plus hashes, never PDFs. Identity and integrity
    split for watermarked retail PDFs (the same module hashes differently per
    customer): cross-copy *identity* is metadata (`title`, `publisher`,
    `edition`, `pages`), while *integrity* is `sha256` when pinned (every
    committed member — the harness refuses a mismatched file before any model
    spend) or the local `source.sha256` sidecar when not (the
    watermarked-retail case; seeded the first time the harness sees the
    module's source). `license` is optional because a private corpus is the
    owner's copy with no redistribution surface — the license-verification
    procedure applies only where something derived will be committed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    source_url: str
    sha256: str | None = None
    pages: int = Field(ge=1)
    publisher: str | None = None
    edition: str | None = None
    license: ManifestLicense | None = None
    truth_provenance: TruthProvenance | None = None

    @field_validator("sha256")
    @classmethod
    def _hex_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return value


def load_truth(path: Path) -> ModuleTruth:
    """Load and validate a corpus truth file.

    Args:
        path: The `truth.yaml` path.

    Returns:
        The validated truth. Unknown keys are rejected — a typo in a
        hand-authored truth file must fail loudly, not silently drop a fact.
    """
    return ModuleTruth.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_manifest(path: Path) -> CorpusManifest:
    """Load and validate a corpus manifest.

    Args:
        path: The `manifest.yaml` path.

    Returns:
        The validated manifest.
    """
    return CorpusManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def sidecar_path(module_dir: Path) -> Path:
    """The module's local integrity sidecar: the copy-specific source hash.

    Only meaningful for manifests without a `sha256` pin (watermarked retail
    PDFs hash differently per customer, so a committed pin would be
    meaningless); its one job is proving later re-runs score the same file
    the truth was authored against. Never committed for repo corpus members —
    they all pin.

    Args:
        module_dir: The corpus member's directory.

    Returns:
        `<module-dir>/source.sha256`.
    """
    return module_dir / "source.sha256"


def enforce_source_integrity(manifest: CorpusManifest, module_dir: Path, digest: str, described: str) -> bool:
    """Enforce the truth-to-source chain of custody for one observed digest.

    The manifest's `sha256` pin gates when present (every committed member);
    otherwise the local sidecar gates, seeded on first sight — at `convert`
    (from the PDF) or, for a workdir converted outside the harness, at first
    `score` (from `run.json`'s recorded source hash). The chain runs unbroken
    from the file the truth was authored against to any published number.

    Args:
        manifest: The module's manifest.
        module_dir: The corpus member's directory (where the sidecar lives).
        digest: The observed source sha256 (from the PDF or from `run.json`).
        described: What was hashed, for the refusal message.

    Returns:
        True when this call seeded the sidecar (the harness's first sight of
        the module's source); False when the digest matched an existing gate.

    Raises:
        ValueError: If the digest matches neither the manifest pin nor the
            sidecar — the source is not the file the truth was authored
            against.
    """
    expected = manifest.sha256
    hint = "download the exact release the manifest records"
    if expected is None:
        sidecar = sidecar_path(module_dir)
        if not sidecar.is_file():
            sidecar.write_text(digest + "\n", encoding="utf-8")
            return True
        expected = sidecar.read_text(encoding="utf-8").strip()
        hint = f"the sidecar {sidecar} records the source the truth was authored against"
    if digest != expected:
        raise ValueError(
            f"{described} has sha256 {digest}, but this module's truth was authored against {expected} — {hint}"
        )
    return False


def verify_source(manifest: CorpusManifest, module_dir: Path, pdf_path: Path) -> bool:
    """Hash a local PDF and enforce the chain of custody, before any model spend.

    Truth authored against one printing scores a different printing as noise
    — the hash, never the URL, is the integrity gate.

    Args:
        manifest: The module's manifest.
        module_dir: The corpus member's directory.
        pdf_path: The locally downloaded PDF.

    Returns:
        True when the call seeded the module's sidecar (first sight).

    Raises:
        ValueError: If the file is not the source the truth was authored
            against.
    """
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    return enforce_source_integrity(manifest, module_dir, digest, str(pdf_path))


class AreaMetrics(BaseModel):
    """The areas family: recall (the headline metric), the hallucination guard, and dungeon alignment.

    The dungeon counts make the survey mode legible in every scoreboard entry
    — a measured mode-flip (ten lairs collapsing into one dungeon on a
    re-roll of the same module) reads as `truth_dungeons=14,
    extracted_dungeons=5` instead of requiring a trip to `survey.json`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_dungeons: int
    """How many dungeons the truth asserts."""

    extracted_dungeons: int
    """How many dungeons the survey extracted."""

    matched_dungeons: int
    """How many truth dungeons aligned to an extracted dungeon."""

    truth_areas: int
    """How many keyed areas the truth asserts, across *all* its dungeons —
    aligned or not, so a whole missed dungeon depresses recall."""

    extracted_areas: int
    """How many keyed areas extraction produced, across all extracted
    dungeons."""

    matched: int
    """How many truth areas matched an extracted area (matching happens
    within aligned dungeons)."""

    recall: float | None
    """`matched / truth_areas`; `None` on an empty denominator."""

    precision: float | None
    """`matched / extracted_areas` — the hallucination guard; `None` on an
    empty denominator."""


class EncounterMetrics(BaseModel):
    """The encounters family: name recall and precision, count accuracy, resolution accuracy, custom-emission accuracy.

    The custom pair scores the truth's `custom: true` assertions against the
    stat-block cache, so emission is its own legible number rather than
    diluting SRD-resolution accuracy; `non_srd` keeps meaning "no SRD
    template and no assertion about emission." The precision triple rides the
    asserted-empty convention: it counts only over matched areas whose truth
    asserts encounters, because only there is an extracted name that matches
    nothing provably a hallucination rather than an unasserted fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_encounters: int
    """How many encounters the truth asserts."""

    name_matched: int
    """How many truth encounters matched an extracted name in their area."""

    name_recall: float | None
    """`name_matched / truth_encounters`; `None` on an empty denominator."""

    precision_denominator: int
    """How many distinct folded extracted names appear in matched areas whose
    truth asserts encounters."""

    precision_matched: int
    """How many of those folds appear in their truth area's fold set."""

    precision: float | None
    """`precision_matched / precision_denominator`; `None` on an empty
    denominator."""

    count_denominator: int
    """How many *name-matched* truth encounters assert a count."""

    count_matched: int
    """How many asserted counts the extraction reproduced."""

    count_accuracy: float | None
    """`count_matched / count_denominator`; `None` on an empty denominator."""

    resolution_denominator: int
    """How many *name-matched* truth encounters assert an SRD template."""

    resolution_matched: int
    """How many asserted templates resolution reproduced."""

    resolution_accuracy: float | None
    """`resolution_matched / resolution_denominator`; `None` on an empty
    denominator."""

    custom_denominator: int = 0
    """How many *name-matched* truth encounters assert custom emission
    (`custom: true`)."""

    custom_matched: int = 0
    """How many custom assertions have a usable cached stat block."""

    custom_accuracy: float | None = None
    """`custom_matched / custom_denominator`; `None` on an empty denominator."""

    non_srd: int
    """Truth encounters asserting no SRD template and nothing about emission."""


class ConnectionMetrics(BaseModel):
    """The connections family: F1 over undirected same-level edges in the asserted universe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_edges: int
    """How many undirected edges the truth asserts."""

    extracted_edges: int
    """How many undirected edges extraction produced within the asserted
    universe."""

    true_positives: int
    """The edges both agree on."""

    precision: float | None
    """`true_positives / extracted_edges`; `None` on an empty denominator."""

    recall: float | None
    """`true_positives / truth_edges`; `None` on an empty denominator."""

    f1: float | None
    """The harmonic mean of precision and recall; `None` when either is."""


class TreasureMetrics(BaseModel):
    """The treasure family: presence agreement and letter accuracy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    presence_denominator: int
    """How many *matched* areas the truth asserts treasure presence (or
    absence) for."""

    presence_matched: int
    """How many of those the extraction agreed with."""

    presence_agreement: float | None
    """`presence_matched / presence_denominator`; `None` on an empty
    denominator."""

    letters_denominator: int
    """How many treasure-type letters the truth asserts on matched areas."""

    letters_matched: int
    """How many asserted letters the extraction reproduced."""

    letter_accuracy: float | None
    """`letters_matched / letters_denominator`; `None` on an empty
    denominator."""


class DoorMetrics(BaseModel):
    """The doors family: presence recall and precision plus kind/locked accuracy, over the asserted door universe.

    The universe is undirected edges between matched areas with at least one
    endpoint asserting `doors` — an asserting area's door set is complete, so
    an extracted door its truth omits is a false positive, and a door fact
    stated on neither directed mention is a miss. The extracted facts flow
    through the edge-fact seam merged with the map reading
    ([`merge_level_edges`][osrforge.reconcile.merge_level_edges]) — the fact
    source phase 11 swapped in without touching these semantics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_doors: int
    """How many door edges the truth asserts within the universe."""

    extracted_doors: int
    """How many door edges extraction stated within the universe."""

    true_positives: int
    """The door edges both agree on."""

    recall: float | None
    """`true_positives / truth_doors`; `None` on an empty denominator."""

    precision: float | None
    """`true_positives / extracted_doors`; `None` on an empty denominator."""

    kind_matched: int
    """How many true positives agree on kind (`door` / `secret_door`)."""

    kind_accuracy: float | None
    """`kind_matched / true_positives`; `None` on an empty denominator."""

    locked_matched: int
    """How many true positives agree on the locked condition."""

    locked_accuracy: float | None
    """`locked_matched / true_positives`; `None` on an empty denominator."""


class TransitionMetrics(BaseModel):
    """The transitions family: dungeon-scoped vertical links matched on truth endpoint pairs.

    Dungeon-scoped because levels are peers: a claim's endpoints resolve
    through the pairing claims to truth `(level, key)` addresses, so a
    printed inter-level stair survives the JN2 collapsed-level shape (one
    extracted level holding two printed levels) and the cross-level keyed
    shape alike. Kind is a resolved attribute of the merged link — matching
    is by endpoints alone, so the kind row can genuinely miss.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asserted_dungeons: int
    """How many aligned dungeons assert `transitions`."""

    truth_transitions: int
    """How many vertical links those dungeons' truth asserts."""

    extracted_transitions: int
    """How many deduplicated vertical links extraction claimed in those
    dungeons."""

    true_positives: int
    """The links both agree on."""

    recall: float | None
    """`true_positives / truth_transitions`; `None` on an empty denominator."""

    precision: float | None
    """`true_positives / extracted_transitions`; `None` on an empty
    denominator."""

    kind_matched: int
    """How many true positives agree on kind (`stairs` / `trapdoor` /
    `chute`)."""

    kind_accuracy: float | None
    """`kind_matched / true_positives`; `None` on an empty denominator."""


class EntranceMetrics(BaseModel):
    """The entrance family: does the pipeline's entrance selection pick the printed way in?

    Scores geometry's own selection through the shared
    [`select_entrance`][osrforge.reconcile.select_entrance] — a resolvable
    map proposal beating the positional heuristic — so the metric and
    geometry can never pick differently.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asserted: int
    """How many aligned dungeons assert `entrance`."""

    matched: int
    """How many of those the selection heuristic agreed with."""

    accuracy: float | None
    """`matched / asserted`; `None` on an empty denominator."""


class ModuleMetrics(BaseModel):
    """One module's metrics block: the seven pinned families."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    areas: AreaMetrics
    """The areas family."""

    encounters: EncounterMetrics
    """The encounters family."""

    connections: ConnectionMetrics
    """The connections family."""

    treasure: TreasureMetrics
    """The treasure family."""

    doors: DoorMetrics
    """The doors family."""

    transitions: TransitionMetrics
    """The transitions family."""

    entrances: EntranceMetrics
    """The entrance family."""


class RunInfo(BaseModel):
    """One recorded run's metadata — injectable so scoring stays deterministic in tests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    model_id: str
    osrforge_version: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    usd: float = Field(ge=0.0)


class ModuleScore(BaseModel):
    """One module's scoreboard entry: the run that produced it, its yardstick, its knobs, and its metrics.

    `truth_sha256` hashes the `truth.yaml` the metrics were scored against —
    recorded at score time so a truth edit between scoring and publishing is
    detectable, and the published pin always names the yardstick that
    actually produced the numbers. `settings_overrides` echoes the scored
    workdir's non-default `ConversionSettings` knobs as `key=value` strings
    (knob names and page numbers, never module text) — a run measured with,
    say, a blanked page is visible in the record instead of being an
    invisible special condition.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: RunInfo
    truth_sha256: str
    settings_overrides: tuple[str, ...] = ()
    metrics: ModuleMetrics


class Scoreboard(BaseModel):
    """A corpus's scoreboard: per-module scores keyed by corpus module id, sorted for byte stability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    modules: dict[str, ModuleScore] = {}

    @field_validator("modules")
    @classmethod
    def _keys_sorted(cls, value: dict[str, ModuleScore]) -> dict[str, ModuleScore]:
        return dict(sorted(value.items()))


def load_scoreboard(path: Path) -> Scoreboard:
    """Load a corpus's scoreboard.

    Args:
        path: The `scoreboard.json` path.

    Returns:
        The scoreboard; an empty one if the file does not exist yet.
    """
    if not path.is_file():
        return Scoreboard()
    return Scoreboard.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_scoreboard(path: Path, scoreboard: Scoreboard) -> None:
    """Write the scoreboard in the pinned artifact byte format.

    Args:
        path: The `scoreboard.json` path.
        scoreboard: The scoreboard to persist.
    """
    write_json_artifact(path, scoreboard)


def settings_overrides(settings: ConversionSettings) -> tuple[str, ...]:
    """The non-default conversion knobs, as `key=value` strings in field order.

    Values render as YAML-parseable text (`blank_page_renders=[21]`,
    `render_dpi=300`) — the same shape the `--set` flag was typed with. Knob
    names and numbers only, never module text.

    Args:
        settings: The settings echoed in a scored workdir's `run.json`.

    Returns:
        One `key=value` string per knob that differs from the default.
    """
    defaults = ConversionSettings()
    dump = settings.model_dump(mode="json")
    overrides: list[str] = []
    for name in ConversionSettings.model_fields:
        if getattr(settings, name) == getattr(defaults, name):
            continue
        value = dump[name]
        overrides.append(f"{name}={value if isinstance(value, str) else json.dumps(value)}")
    return tuple(overrides)


class ByomEntry(BaseModel):
    """One published BYOM record: aggregate-only by construction.

    Identity metadata (cross-copy: title, publisher, edition, pages), the run
    block, the truth-file hash, the non-default knobs, and the metrics —
    nothing else. No PDF hash (copy-specific, meaningless cross-customer), no
    license claims, no module text. `truth_sha256` is the yardstick pin:
    watermark-proof because it hashes the owner's YAML, and its job is
    distinguishing "the extraction moved" from "the truth moved" between
    entries.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    publisher: str | None = None
    edition: str | None = None
    pages: int = Field(ge=1)
    run: RunInfo
    truth_sha256: str
    settings_overrides: tuple[str, ...] = ()
    metrics: ModuleMetrics


class ByomScoreboard(BaseModel):
    """The committed BYOM scoreboard: advisory, aggregate-only, owner-refreshed.

    Answers "how does it perform in general," not "may this PR merge" — the
    regression rule binds the corpus scoreboard, never this one. Entries
    refresh best-effort by whoever owns the module; a stale entry is visible
    via its `osrforge_version` stamp, never blocking.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    modules: dict[str, ByomEntry] = {}

    @field_validator("modules")
    @classmethod
    def _keys_sorted(cls, value: dict[str, ByomEntry]) -> dict[str, ByomEntry]:
        return dict(sorted(value.items()))


def load_byom_scoreboard(path: Path) -> ByomScoreboard:
    """Load the committed BYOM scoreboard.

    Args:
        path: The `byom-scoreboard.json` path.

    Returns:
        The board; an empty one if the file does not exist yet.
    """
    if not path.is_file():
        return ByomScoreboard()
    return ByomScoreboard.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_byom_scoreboard(path: Path, board: ByomScoreboard) -> None:
    """Write the BYOM scoreboard in the pinned artifact byte format.

    Args:
        path: The `byom-scoreboard.json` path.
        board: The board to persist.
    """
    write_json_artifact(path, board)


def publish_module(
    board: ByomScoreboard,
    module_id: str,
    manifest: CorpusManifest,
    private_board: Scoreboard,
    current_truth_sha256: str,
    committed_ids: Collection[str],
) -> ByomScoreboard:
    """Copy one private scoreboard entry onto the committed BYOM board.

    The deliberate, outward-facing act, separate from scoring. The chain of
    custody holds because only scored entries are copied, scoring runs under
    the source-integrity check, and the published yardstick pin is the hash
    recorded *at score time* — a truth edited after scoring is refused, not
    silently paired with stale metrics.

    Args:
        board: The current committed BYOM board.
        module_id: The private corpus module id to publish.
        manifest: The module's manifest (identity plus provenance).
        private_board: The private corpus's scoreboard.
        current_truth_sha256: The hash of the module's `truth.yaml` as it
            stands now, compared against the score-time hash.
        committed_ids: The committed corpus's module ids (the shared-namespace guard).

    Returns:
        A new board with the module's entry added or replaced.

    Raises:
        ValueError: On any pinned refusal — no scored entry for the id, a
            truth file that changed since scoring, missing truth provenance,
            id collision with a committed corpus member, or (on update) a
            title mismatch with the entry being replaced.
    """
    score = private_board.modules.get(module_id)
    if score is None:
        raise ValueError(f"no scored entry for {module_id!r} in the private scoreboard — score before publishing")
    if score.truth_sha256 != current_truth_sha256:
        raise ValueError(
            f"{module_id!r}'s truth.yaml changed since its entry was scored — re-score before publishing, "
            "so the published metrics and yardstick pin describe the same truth"
        )
    if manifest.truth_provenance is None:
        raise ValueError(
            f"{module_id!r} has no truth_provenance in its manifest — unverified truth can be scored locally, "
            "but it cannot put numbers on the committed board (see tools/eval/AUTHORING.md)"
        )
    if module_id in committed_ids:
        raise ValueError(
            f"{module_id!r} collides with a committed corpus member — the BYOM scoreboard shares a namespace "
            "with nothing; rename the private corpus directory"
        )
    existing = board.modules.get(module_id)
    if existing is not None and existing.title != manifest.title:
        raise ValueError(
            f"{module_id!r} is already published as {existing.title!r}, but this manifest says "
            f"{manifest.title!r} — two modules cannot share one id; rename the private corpus directory"
        )
    entry = ByomEntry(
        title=manifest.title,
        publisher=manifest.publisher,
        edition=manifest.edition,
        pages=manifest.pages,
        run=score.run,
        truth_sha256=score.truth_sha256,
        settings_overrides=score.settings_overrides,
        metrics=score.metrics,
    )
    return ByomScoreboard(
        schema_version=board.schema_version,
        modules={**board.modules, module_id: entry},
    )


def _stale_module_ids(modules: dict[str, Any]) -> list[str]:
    """The raw board entries the extended models refuse, sorted — the loud half of the rescore refusal."""
    stale: list[str] = []
    for module_id, entry in modules.items():
        try:
            ModuleScore.model_validate(entry)
        except ValidationError:
            stale.append(module_id)
    return sorted(stale)


def rescore_modules(board_path: Path, targets: Sequence[tuple[str, Path, Path]]) -> dict[str, ModuleMetrics]:
    """Re-score existing scoreboard entries' workdirs against current truth — one rebuilt board, one save.

    The offline-regeneration mechanism: same runs, re-scored. The stale board
    is read as raw JSON — never through the models, whose required fields a
    pre-extension entry cannot satisfy — and each rebuilt entry carries its
    raw run block verbatim (the run's own date, tokens, cost, model id,
    package version: the entry stays a record of the run) plus the carried
    `settings_overrides`, a freshly pinned `truth_sha256`, and the fresh
    metrics from [`score_workdir`][osrforge.evals.score_workdir]. The entire
    rebuilt board then validates through the extended models before the
    single save, so a save never persists a board the current schema rejects.

    Taking the targets together is what makes a schema migration saveable at
    all: a board whose every entry predates a model extension can only become
    valid whole, so its entries rebuild in one invocation and the board saves
    once. A leftover entry the extended models still refuse is a loud
    refusal naming the stale ids — the whole-board pin working, not an
    obstacle to route around. A single target stays valid whenever the rest
    of the board is already current-shaped.

    Args:
        board_path: The corpus's `scoreboard.json`; must exist.
        targets: `(module id, workdir path, truth path)` per entry to
            rebuild, each id named once. Every id must already hold an entry
            — a module with no scored run has no run block to carry, and a
            new run records itself through `score --update-scoreboard`.

    Returns:
        Module id → the fresh metrics written into its entry, in target
        order.

    Raises:
        ValueError: If no targets are given, an id repeats, the board does
            not exist, an id holds no entry, or the rebuilt board still
            contains stale entries (named in the message) — nothing is
            written.
        pydantic.ValidationError: If the rebuilt board fails the extended
            models for any other reason — nothing is written.
    """
    if not targets:
        raise ValueError("rescore needs at least one (module id, workdir, truth) target")
    names = [module_id for module_id, _, _ in targets]
    if len(set(names)) != len(names):
        raise ValueError(f"a module id repeats across the rescore targets: {names} — name each entry once")
    if not board_path.is_file():
        raise ValueError(f"no scoreboard at {board_path} — rescore regenerates existing entries only")
    raw: dict[str, Any] = json.loads(board_path.read_text(encoding="utf-8"))
    raw_modules = raw.get("modules")
    existing = cast(dict[str, Any], raw_modules) if isinstance(raw_modules, dict) else {}
    missing = sorted(module_id for module_id in names if module_id not in existing)
    if missing:
        raise ValueError(
            f"no existing entry for {', '.join(missing)} in {board_path} — rescore regenerates scored entries; "
            "a new run records itself through score --update-scoreboard"
        )
    results: dict[str, ModuleMetrics] = {}
    for module_id, workdir_path, truth_path in targets:
        truth = load_truth(truth_path)
        metrics = score_workdir(workdir_path, truth)
        entry: dict[str, Any] = existing[module_id]
        existing[module_id] = {
            "run": entry["run"],
            "truth_sha256": hashlib.sha256(truth_path.read_bytes()).hexdigest(),
            "settings_overrides": entry.get("settings_overrides", []),
            "metrics": metrics.model_dump(mode="json"),
        }
        results[module_id] = metrics
    stale = _stale_module_ids(existing)
    if stale:
        raise ValueError(
            f"the rebuilt board still holds stale entries: {', '.join(stale)} — name them in the same rescore "
            "invocation (each with its retained workdir) so the board saves once, whole and valid"
        )
    board = Scoreboard.model_validate(raw)
    save_scoreboard(board_path, board)
    return results


def _ratio(numerator: int, denominator: int) -> float | None:
    """A metric ratio, rounded for scoreboard readability; None when the denominator is empty."""
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _truth_key_slug(area: TruthArea, position: int) -> str:
    """A truth area's canonical matching slug, with the same positional fallback normalization applies."""
    return canonical_slug(area.key) or f"area-{position}"


def _dungeon_key_sets(truth: ModuleTruth) -> list[set[str]]:
    return [
        {
            _truth_key_slug(area, position)
            for level in dungeon.levels
            for position, area in enumerate(level.areas, start=1)
        }
        for dungeon in truth.dungeons
    ]


def _align_dungeons(truth: ModuleTruth, index: SurveyIndex) -> dict[int, int]:
    """Align truth dungeons to extracted dungeons, deterministically.

    Greedy by area-key-set overlap (both sides' keys through
    `canonical_slug`), each extracted dungeon matched at most once, truth
    dungeons processed in truth-file order. Candidate ties break by
    `difflib.SequenceMatcher` ratio over name slugs, then by extracted
    document order — a total order, so alignment is deterministic by
    construction.

    Args:
        truth: The module truth.
        index: The extracted survey index.

    Returns:
        Truth dungeon position → extracted dungeon position, for the matched
        pairs only. An unmatched truth dungeon counts all its areas as misses.
    """
    truth_sets = _dungeon_key_sets(truth)
    extracted_sets = [{area.key for level in dungeon.levels for area in level.areas} for dungeon in index.dungeons]
    taken: set[int] = set()
    matches: dict[int, int] = {}
    for truth_position, (truth_dungeon, truth_keys) in enumerate(zip(truth.dungeons, truth_sets, strict=True)):
        truth_name_slug = canonical_slug(truth_dungeon.name)
        best: tuple[int, float, int] | None = None  # (overlap, name ratio, -position) maximized
        best_position: int | None = None
        for extracted_position, extracted_keys in enumerate(extracted_sets):
            if extracted_position in taken:
                continue
            overlap = len(truth_keys & extracted_keys)
            if overlap == 0:
                continue
            extracted_name_slug = canonical_slug(index.dungeons[extracted_position].name)
            ratio = SequenceMatcher(None, truth_name_slug, extracted_name_slug).ratio()
            candidate = (overlap, ratio, -extracted_position)
            if best is None or candidate > best:
                best = candidate
                best_position = extracted_position
        if best_position is not None:
            taken.add(best_position)
            matches[truth_position] = best_position
    return matches


def _align_levels(truth_dungeon: TruthDungeon, extracted_dungeon: SurveyDungeon) -> dict[int, int]:
    """Align one aligned dungeon's truth levels to extracted levels by maximal area-key overlap.

    Many-to-one from the truth side: each truth level independently pairs with
    the extracted level sharing the most canonical-slug area keys — ties break
    by smaller level-number distance, then by lower extracted level number —
    and a truth level with zero overlap everywhere stays unmatched. Several
    truth levels pairing with one extracted level is the B4 shape (10 printed
    tiers grouped by extraction into 6 coarse levels), which is why one-to-one
    number matching cannot heal it. The recorded hazard: overlap alignment
    assumes area keys distinguish levels; a module keying every level 1..N and
    extracting partially could cross-pair, which the number-distance tie-break
    absorbs only for equal overlaps.

    Args:
        truth_dungeon: The truth dungeon.
        extracted_dungeon: The extracted dungeon it aligned to.

    Returns:
        Truth level number → extracted level number, matched pairs only.
    """
    extracted_keys = {level.number: {area.key for area in level.areas} for level in extracted_dungeon.levels}
    matches: dict[int, int] = {}
    for level in truth_dungeon.levels:
        truth_keys = {_truth_key_slug(area, position) for position, area in enumerate(level.areas, start=1)}
        best: tuple[int, int, int] | None = None  # (overlap, -distance, -number) maximized
        best_number: int | None = None
        for number in sorted(extracted_keys):
            overlap = len(truth_keys & extracted_keys[number])
            if overlap == 0:
                continue
            candidate = (overlap, -abs(level.number - number), -number)
            if best is None or candidate > best:
                best = candidate
                best_number = number
        if best_number is not None:
            matches[level.number] = best_number
    return matches


def _load_level_cache(workdir: Workdir, dungeon_id: str, level_number: int) -> LevelContent:
    path = workdir.areas_json(dungeon_id, level_number)
    if not path.is_file():
        raise ValueError(f"a level's content cache is missing: {path} — evals score completed extractions")
    return LevelContent.model_validate_json(path.read_text(encoding="utf-8"))


def _match_fold(name: str) -> str:
    """Fold a normalized name's plural morphology for truth-to-extraction matching.

    Truth encounter names are singular by authoring convention while extraction
    records the name as printed, usually plural (`6 Orcs` → truth `orc`,
    extracted `orcs`). Matching compares folded forms on *both* sides, so the
    fold need not produce a correct English singular — only fold a name's
    singular and plural to the same string. The ruleset is deliberately
    morphological and minimal, pinned: `men` → `man` per token; otherwise a
    trailing `s` strips when the token is longer than three characters and does
    not end in `ss`, `us`, or `is`. Token subsets and renames never match — a
    `hobgoblin chief` is not a `hobgoblin`, and a renamed creature is a real
    extraction disagreement the metrics must keep seeing.

    Known misses, recorded: sibilant `-es` plurals (`bosses` → `bosse` ≠
    `boss`), f/v alternations (`wolves` → `wolve` ≠ `wolf`), y-plurals
    (`harpies` → `harpie` ≠ `harpy`), `-y` nouns beside `-ie` ones
    (`cronies` → `cronie` ≠ `crony`), and `-men` compounds (`mermen` ≠
    `merman` — the `men` rule fires on the bare token only) stay misses. Conservative by design —
    the fold never awards false credit, and these classes keep their
    singular/plural jitter until evidence justifies widening.
    """
    tokens: list[str] = []
    for token in name.split(" "):
        if token == "men":
            tokens.append("man")
        elif len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            tokens.append(token[:-1])
        else:
            tokens.append(token)
    return " ".join(tokens)


def _extracted_count(area: AreaContent, matched_names: set[str]) -> int | None:
    """The extracted count for one fold-matched truth name in one area, pinned.

    The sum of `count_fixed` over the area's encounters whose normalized name
    is in the truth name's fold-matched set when every such encounter carries
    one; None (no comparable count) when any of them states dice or nothing.
    """
    counts: list[int] = []
    for encounter in area.encounters:
        if normalize_monster_name(encounter.monster) not in matched_names:
            continue
        if encounter.count_fixed is None:
            return None
        counts.append(encounter.count_fixed)
    if not counts:
        return None
    return sum(counts)


def _treasure_signal(area: AreaContent) -> bool:
    """Whether extraction saw treasure: any non-empty-after-strip cached treasure string, unparsed included."""
    return any(text.strip() for text in area.treasure)


_DOOR_VIAS = ("door", "secret_door")

_VERTICAL_VIAS = ("stairs", "trapdoor", "chute")

_TRANSITION_KIND_ORDER = ("trapdoor", "chute", "stairs")
"""The pinned kind-conflict total order for merged transition mentions:
trapdoor > chute > stairs — arbitrary but deterministic."""


@dataclass(frozen=True)
class _EdgeFact:
    """One undirected extracted edge's merged facts, as collapsed from its directed mentions.

    The pinned dedup rule over the mentions of one edge: `door` if either
    side states a door via; `kind` resolves a conflict to `secret_door` (the
    more specific claim); `locked` if either side states it on a door via —
    door conditions on a non-door via contribute nothing (geometry's discard
    posture). `stated_via` is the first stated (non-`passage`) mechanism in
    mention order — the internal marker that makes the reconciliation's
    absence test match geometry's (`via == "passage"` on every mention is
    the only absence); the door-kind dedup above deliberately keeps its own
    more-specific rule, the recorded asymmetry the shared merge does not
    unify.
    """

    door: bool
    kind: Literal["door", "secret_door"] | None
    locked: bool
    stated_via: str | None = None


def _edge_facts(cache: LevelContent, matched: Collection[str]) -> dict[frozenset[str], _EdgeFact]:
    """The edge-fact seam: one pairing's undirected *prose* edge facts, derived from the level cache.

    Every prose edge fact — presence, door, kind, locked — flows through
    here; `score_workdir` then merges the result with the pairing level's
    map reading through [`merge_level_edges`][osrforge.reconcile.merge_level_edges]
    (the phase 11 reroute this seam was pre-committed for), and both the
    connection F1 and the door family consume the merged set: the F1 reads
    presence (every key), the door family the door facts.

    Endpoints are matched slugs: a mention is an edge only when both its
    areas matched this pairing's truth areas — a level-shaped target
    (`to_key: null`) and an unmatched or self target stay outside the edge
    universe entirely, semantics and denominators untouched.

    Args:
        cache: The pairing's extracted level cache.
        matched: The pairing's matched slugs (extracted keys that matched a
            truth area).

    Returns:
        Undirected endpoint pair → merged prose edge fact.
    """
    facts: dict[frozenset[str], _EdgeFact] = {}
    for area in cache.areas:
        if area.key not in matched:
            continue
        for connection in area.connections:
            if connection.to_key is None:
                continue
            to_key = canonical_slug(connection.to_key)
            if to_key not in matched or to_key == area.key:
                continue
            pair = frozenset({area.key, to_key})
            fact = facts.get(pair, _EdgeFact(door=False, kind=None, locked=False))
            stated_via = (
                fact.stated_via
                if fact.stated_via is not None
                else (connection.via if connection.via != "passage" else None)
            )
            if connection.via in _DOOR_VIAS:
                fact = _EdgeFact(
                    door=True,
                    kind="secret_door" if "secret_door" in (connection.via, fact.kind) else "door",
                    locked=fact.locked or connection.door_locked,
                    stated_via=stated_via,
                )
            else:
                fact = _EdgeFact(door=fact.door, kind=fact.kind, locked=fact.locked, stated_via=stated_via)
            facts[pair] = fact
    return facts


@dataclass(frozen=True)
class _Pairing:
    """One truth-level-to-extracted-level pairing's record, kept for the post-loop dungeon-scoped families."""

    truth_level: TruthLevel
    extracted_number: int
    matched: dict[str, TruthArea]
    cache: LevelContent


@dataclass(frozen=True)
class _DungeonPairings:
    """One aligned dungeon's pairing records — the claim registry the transition and entrance families read."""

    truth_dungeon: TruthDungeon
    extracted_dungeon: SurveyDungeon
    level_matches: dict[int, int]
    pairings: list[_Pairing]


def _truth_endpoint(dungeon: TruthDungeon, level_number: int, key: str) -> tuple[int, str]:
    """A truth transition or entrance endpoint as `(level number, matching slug)`.

    The slug is the same `_truth_key_slug` the pairing claims carry, so
    endpoint comparison and area matching can never use two spellings of one
    key. The dungeon validator guarantees the endpoint exists.
    """
    level = next(level for level in dungeon.levels if level.number == level_number)
    key_slug = canonical_slug(key)
    for position, area in enumerate(level.areas, start=1):
        if canonical_slug(area.key) == key_slug:
            return (level_number, _truth_key_slug(area, position))
    raise AssertionError(f"truth endpoint {key!r} missing from level {level_number} — the dungeon validator gates this")


def _resolved_transition_kind(kinds: Collection[str]) -> str:
    """A merged transition claim's resolved kind attribute, under the pinned total order."""
    return next(kind for kind in _TRANSITION_KIND_ORDER if kind in kinds)


def _dungeon_transition_claims(
    record: _DungeonPairings,
) -> tuple[dict[frozenset[tuple[int, str]], list[str]], dict[tuple[tuple[int, str], int], list[str]]]:
    """One asserting dungeon's deduplicated vertical claims: pair-form and stub-form, with their mention kinds.

    The pinned four-clause classifier over each matched area's connection
    mentions, ordered and total:

    1. `to_level` set and `to_key` null → a vertical claim in stub form,
       keyed by `(source endpoint, target level number)`. A mention carrying
       *both* takes the keyed path, falling back to stub form only when the
       key does not resolve.
    2. A keyed target that resolves through the claim registry → a vertical
       claim iff its truth level differs from the source's *and* the mention
       carries a vertical signal (via in stairs/trapdoor/chute, or an
       up/down direction — mirroring geometry). A same-truth-level target is
       a same-level edge and only that; a different-level target with no
       vertical signal is neither — one extraction claim is never scored in
       two families.
    3. A keyed target that resolves nowhere (and no `to_level`) → dropped,
       mirroring the connection F1's conservatism.
    4. No target of any kind → dropped, mirroring geometry's
       `no target stated` discard.

    Keyed-target lookup order, pinned: the source's own extracted level
    first, then sibling pairings in survey order — geometry's
    resolve-locally-then-on-siblings. After classification, a stub-form
    claim merges into a pair-form claim of the same physical link (same
    source endpoint, pair far endpoint on the stub's target level; the
    richer pair form wins), first pair claim in derivation order on the
    vanishingly rare tie.
    """
    claims: dict[tuple[int, str], tuple[int, str]] = {}
    for pairing in record.pairings:
        for slug in pairing.matched:
            claims[(pairing.extracted_number, slug)] = (pairing.truth_level.number, slug)
    survey_numbers = [level.number for level in record.extracted_dungeon.levels]

    def resolve(source_extracted: int, slug: str) -> tuple[int, str] | None:
        hit = claims.get((source_extracted, slug))
        if hit is not None:
            return hit
        for number in survey_numbers:
            if number == source_extracted:
                continue
            hit = claims.get((number, slug))
            if hit is not None:
                return hit
        return None

    pair_claims: dict[frozenset[tuple[int, str]], list[str]] = {}
    stub_claims: dict[tuple[tuple[int, str], int], list[str]] = {}
    for pairing in record.pairings:
        for area in pairing.cache.areas:
            if area.key not in pairing.matched:
                continue
            source = (pairing.truth_level.number, area.key)
            for connection in area.connections:
                kind = transition_via(connection.via)
                target: tuple[int, str] | None = None
                if connection.to_key is not None:
                    target = resolve(pairing.extracted_number, canonical_slug(connection.to_key))
                if target is not None:
                    if target[0] == source[0]:
                        continue
                    if connection.via not in _VERTICAL_VIAS and connection.direction not in ("up", "down"):
                        continue
                    pair_claims.setdefault(frozenset({source, target}), []).append(kind)
                elif connection.to_level is not None:
                    stub_claims.setdefault((source, connection.to_level), []).append(kind)
    for stub_key, kinds in list(stub_claims.items()):
        source, to_level = stub_key
        for pair, pair_kinds in pair_claims.items():
            if source in pair and next(endpoint for endpoint in pair if endpoint != source)[0] == to_level:
                pair_kinds.extend(kinds)
                del stub_claims[stub_key]
                break
    return pair_claims, stub_claims


def score_workdir(workdir_path: Path, truth: ModuleTruth) -> ModuleMetrics:
    """Score one converted workdir's stage caches against a module's truth.

    Reads `stages/survey.json` (area recall/precision, the entrance
    selection), the `stages/areas.*.json` content caches (encounters,
    connections, doors, transitions, treasure), `stages/monsters.json`
    (resolution accuracy), `stages/statblocks.json` (custom-emission
    accuracy — a missing file scores no matches, the honest state of a
    workdir converted before the stat-block pass existed, never an error),
    and `stages/mapread.json` (the map readings the edge and entrance
    families reconcile with — absent tolerated under the same posture: the
    honest state of an older workdir, scored prose-only, never an error).
    Deterministic: scoring the same workdir twice yields byte-identical
    metrics.

    Encounter names match under a minimal morphological fold (`_match_fold`) —
    the truth's singular authoring convention meets extraction's printed
    plural on folded forms; a truth encounter's count compares against the
    fold-matched encounter group's summed fixed counts, and its resolution
    matches only when every fold-matched extracted name resolved to the
    asserted template. A custom-asserted encounter matches only when every
    fold-matched extracted name carries a *usable* block in the stat-block
    cache — usability being exactly assembly's refusal-ladder predicate,
    shared as one helper, so the metric can never score an emission assembly
    would refuse; the signal is honest by construction, because the pass only
    runs over unresolved names, so a wrongly-SRD-resolved bespoke creature
    has no block and scores a miss. An area whose truth lists one name twice
    (two separately statted groups printed under one name) scores each entry
    against the whole group — the summed count can then match neither entry
    and resolution can credit at most one of the two templates; a known
    conservative shape, recorded rather than special-cased.

    The edge families ride the edge-fact seam (`_edge_facts`) rerouted
    through [`merge_level_edges`][osrforge.reconcile.merge_level_edges]: the
    prose facts merge with the pairing level's map reading — endpoints
    resolved exactly-then-slug, map-only pairs filtered to matched slugs —
    and the merged set flows through the unchanged asserted-universe gates,
    so map-only edges and map-adopted doors enter exactly where truth
    asserts. Transitions and the entrance score dungeon-scoped, after the
    level loop, over each aligned dungeon's recorded pairing claims — the
    entrance through the shared
    [`select_entrance`][osrforge.reconcile.select_entrance], so the metric
    and geometry can never pick differently.

    Args:
        workdir_path: A workdir whose extraction stages have completed.
        truth: The module's ground truth.

    Returns:
        The seven metric families.

    Raises:
        ValueError: If a required stage cache is missing.

    Examples:
        ```python
        from pathlib import Path

        from osrforge.evals import load_truth, score_workdir

        truth = load_truth(Path("tools/eval/corpus/minimod/truth.yaml"))
        metrics = score_workdir(Path("minimod.forge"), truth)
        print(metrics.areas.recall, metrics.encounters)
        ```
    """
    workdir = Workdir(workdir_path)
    if not workdir.survey_json.is_file():
        raise ValueError(f"the survey cache is missing: {workdir.survey_json} — evals score completed extractions")
    if not workdir.monsters_json.is_file():
        raise ValueError(f"the monsters cache is missing: {workdir.monsters_json} — evals score completed extractions")
    index = SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
    resolutions = MonsterResolutions.model_validate_json(workdir.monsters_json.read_text(encoding="utf-8"))
    usable_names: frozenset[str] = frozenset()
    if workdir.statblocks_json.is_file():
        statblocks = StatBlocks.model_validate_json(workdir.statblocks_json.read_text(encoding="utf-8"))
        usable_names = frozenset(name for name, block in statblocks.blocks.items() if usable_stat_block(block))
    readings: dict[tuple[str, int], MapLevelReading] = {}
    if workdir.mapread_json.is_file():
        map_reading = MapReading.model_validate_json(workdir.mapread_json.read_text(encoding="utf-8"))
        readings = {(reading.dungeon_id, reading.level_number): reading for reading in map_reading.levels}

    matches = _align_dungeons(truth, index)

    truth_area_count = sum(len(level.areas) for dungeon in truth.dungeons for level in dungeon.levels)
    extracted_area_count = sum(len(level.areas) for dungeon in index.dungeons for level in dungeon.levels)
    matched_areas = 0

    truth_encounters = 0
    name_matched = 0
    precision_denominator = 0
    precision_matched = 0
    count_denominator = 0
    count_matched = 0
    resolution_denominator = 0
    resolution_matched = 0
    custom_denominator = 0
    custom_matched = 0
    non_srd = 0

    truth_edges: set[tuple[str, str, int, frozenset[str]]] = set()
    extracted_edges: set[tuple[str, str, int, frozenset[str]]] = set()

    truth_doors: dict[tuple[str, str, int, frozenset[str]], TruthDoor] = {}
    extracted_doors: dict[tuple[str, str, int, frozenset[str]], _EdgeFact] = {}

    presence_denominator = 0
    presence_matched = 0
    letters_denominator = 0
    letters_matched = 0

    dungeon_records: list[_DungeonPairings] = []

    for truth_position, truth_dungeon in enumerate(truth.dungeons):
        for level in truth_dungeon.levels:
            truth_encounters += sum(len(area.encounters) for area in level.areas if area.encounters is not None)

        extracted_position = matches.get(truth_position)
        if extracted_position is None:
            continue
        extracted_dungeon = index.dungeons[extracted_position]
        level_matches = _align_levels(truth_dungeon, extracted_dungeon)
        pairings: list[_Pairing] = []
        # Several truth levels may pair with one extracted level, so an
        # extracted area key must match at most one truth area across all of
        # them: pairings process in truth-level order, and a claimed key is
        # not claimed again.
        claimed: dict[int, set[str]] = {}

        for level in truth_dungeon.levels:
            extracted_number = level_matches.get(level.number)
            if extracted_number is None:
                continue
            cache = _load_level_cache(workdir, extracted_dungeon.id, extracted_number)
            cached_areas = {area.key: area for area in cache.areas}
            level_claimed = claimed.setdefault(extracted_number, set())

            matched: dict[str, TruthArea] = {}
            asserted: set[str] = set()
            for position, truth_area in enumerate(level.areas, start=1):
                slug = _truth_key_slug(truth_area, position)
                if truth_area.connections is not None:
                    asserted.add(slug)
                if slug in cached_areas and slug not in level_claimed:
                    matched[slug] = truth_area
                    level_claimed.add(slug)
            matched_areas += len(matched)
            pairings.append(
                _Pairing(truth_level=level, extracted_number=extracted_number, matched=matched, cache=cache)
            )

            # Encounters and treasure, per matched truth area.
            for position, truth_area in enumerate(level.areas, start=1):
                slug = _truth_key_slug(truth_area, position)
                if matched.get(slug) is not truth_area:
                    continue
                extracted_area = cached_areas[slug]
                folded_names: dict[str, set[str]] = {}
                for encounter in extracted_area.encounters:
                    name = normalize_monster_name(encounter.monster)
                    folded_names.setdefault(_match_fold(name), set()).add(name)
                if truth_area.encounters is not None:
                    # Precision only where the truth asserts the complete
                    # list: an unmatched fold in an unasserted area is an
                    # unasserted fact, not a hallucination.
                    truth_folds = {
                        _match_fold(normalize_monster_name(truth_encounter.name))
                        for truth_encounter in truth_area.encounters
                    }
                    precision_denominator += len(folded_names)
                    precision_matched += sum(1 for fold in folded_names if fold in truth_folds)
                for truth_encounter in truth_area.encounters or ():
                    normalized = normalize_monster_name(truth_encounter.name)
                    matched_names = folded_names.get(_match_fold(normalized))
                    if matched_names is None:
                        continue
                    name_matched += 1
                    if truth_encounter.count is not None:
                        count_denominator += 1
                        if _extracted_count(extracted_area, matched_names) == truth_encounter.count:
                            count_matched += 1
                    if truth_encounter.template is None:
                        if truth_encounter.custom:
                            custom_denominator += 1
                            if matched_names <= usable_names:
                                custom_matched += 1
                        else:
                            non_srd += 1
                    else:
                        resolution_denominator += 1
                        resolved = {
                            resolution.template_id if resolution is not None else None
                            for resolution in (resolutions.resolutions.get(name) for name in matched_names)
                        }
                        if resolved == {truth_encounter.template}:
                            resolution_matched += 1

                if truth_area.treasure is not None:
                    presence_denominator += 1
                    if _treasure_signal(extracted_area) == truth_area.treasure.present:
                        presence_matched += 1
                    if truth_area.treasure.letters:
                        letters_denominator += 1
                        parsed = parse_treasure(extracted_area.treasure)
                        if sorted(parsed.letters) == sorted(truth_area.treasure.letters):
                            letters_matched += 1

            # Connections and doors, off the shared edge-fact seam.
            # Connections: undirected same-level edges between matched areas,
            # in the asserted universe (at least one endpoint's neighbor set
            # asserted — an asserted area's list is complete, so any extracted
            # edge incident to it is scoreable). Doors: the same edges,
            # restricted to the doors-asserting universe.
            level_id = (truth_dungeon.name, extracted_dungeon.id, level.number)
            doors_asserting = {slug for slug, truth_area in matched.items() if truth_area.doors is not None}
            for position, truth_area in enumerate(level.areas, start=1):
                if truth_area.connections is None:
                    continue
                slug = _truth_key_slug(truth_area, position)
                if matched.get(slug) is not truth_area:
                    continue
                for neighbor_key in truth_area.connections:
                    neighbor = canonical_slug(neighbor_key)
                    if neighbor in matched and neighbor != slug:
                        truth_edges.add((*level_id, frozenset({slug, neighbor})))
                for neighbor_key, door in (truth_area.doors or {}).items():
                    neighbor = canonical_slug(neighbor_key)
                    if neighbor in matched and neighbor != slug:
                        # Both-endpoint assertions agree by validator, so the
                        # overwrite on the reciprocal visit is a no-op.
                        truth_doors[(*level_id, frozenset({slug, neighbor}))] = door
            # The phase 11 reroute: the prose facts merge with the pairing
            # level's map reading through the shared policy — an adopted
            # door fills a prose absence in place, a map-only pair (both
            # endpoints matched) joins the fact set — and the merged set
            # flows through the unchanged asserted-universe gates below.
            facts = _edge_facts(cache, matched)
            survey_level_keys = [
                area.key
                for survey_level in extracted_dungeon.levels
                if survey_level.number == extracted_number
                for area in survey_level.areas
            ]
            merged = merge_level_edges(
                {
                    pair: ProseEdge(stated_via=fact.stated_via, door_kind=fact.kind if fact.door else None)
                    for pair, fact in facts.items()
                },
                readings.get((extracted_dungeon.id, extracted_number)),
                survey_level_keys,
            )
            for pair, adopted_kind in merged.adopted_doors.items():
                fact = facts[pair]
                facts[pair] = _EdgeFact(door=True, kind=adopted_kind, locked=False, stated_via=fact.stated_via)
            for first, second, adopted_kind in merged.map_only:
                if first not in matched or second not in matched:
                    continue
                facts[frozenset({first, second})] = _EdgeFact(
                    door=adopted_kind is not None, kind=adopted_kind, locked=False
                )
            for pair, fact in facts.items():
                if pair & asserted:
                    extracted_edges.add((*level_id, pair))
                if fact.door and pair & doors_asserting:
                    extracted_doors[(*level_id, pair)] = fact

        dungeon_records.append(
            _DungeonPairings(
                truth_dungeon=truth_dungeon,
                extracted_dungeon=extracted_dungeon,
                level_matches=level_matches,
                pairings=pairings,
            )
        )

    true_positives = len(truth_edges & extracted_edges)
    precision = _ratio(true_positives, len(extracted_edges))
    recall = _ratio(true_positives, len(truth_edges))
    f1: float | None = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    elif precision is not None and recall is not None:
        f1 = 0.0

    door_pairs = truth_doors.keys() & extracted_doors.keys()
    door_kind_matched = sum(1 for pair in door_pairs if extracted_doors[pair].kind == truth_doors[pair].kind)
    door_locked_matched = sum(1 for pair in door_pairs if extracted_doors[pair].locked == truth_doors[pair].locked)

    # Transitions: a dungeon-scoped pass over aligned dungeons whose truth
    # asserts the complete vertical-link set — assertion-aware on both sides,
    # like connections, so an unasserted dungeon's claims are never false
    # positives and its links never misses.
    transition_asserted_dungeons = 0
    truth_transition_count = 0
    extracted_transition_count = 0
    transition_true_positives = 0
    transition_kind_matched = 0
    for record in dungeon_records:
        if record.truth_dungeon.transitions is None:
            continue
        transition_asserted_dungeons += 1
        truth_entries = [
            (
                frozenset(
                    {
                        _truth_endpoint(record.truth_dungeon, transition.from_level, transition.from_key),
                        _truth_endpoint(record.truth_dungeon, transition.to_level, transition.to_key),
                    }
                ),
                transition.kind,
            )
            for transition in record.truth_dungeon.transitions
        ]
        truth_transition_count += len(truth_entries)
        pair_claims, stub_claims = _dungeon_transition_claims(record)
        extracted_transition_count += len(pair_claims) + len(stub_claims)
        taken = [False] * len(truth_entries)
        # Pair-form claims match first (the richer form), then stubs claim
        # what remains: a stub matches on its source endpoint plus the far
        # endpoint's level *number*, compared directly — the landing key is
        # geometry's guess policy, not extraction's claim.
        for pair, kinds in pair_claims.items():
            for position, (truth_pair, truth_kind) in enumerate(truth_entries):
                if taken[position] or truth_pair != pair:
                    continue
                taken[position] = True
                transition_true_positives += 1
                if _resolved_transition_kind(kinds) == truth_kind:
                    transition_kind_matched += 1
                break
        for (source, to_level), kinds in stub_claims.items():
            for position, (truth_pair, truth_kind) in enumerate(truth_entries):
                if taken[position] or source not in truth_pair:
                    continue
                if next(endpoint for endpoint in truth_pair if endpoint != source)[0] != to_level:
                    continue
                taken[position] = True
                transition_true_positives += 1
                if _resolved_transition_kind(kinds) == truth_kind:
                    transition_kind_matched += 1
                break

    # The entrance: the shared selection — geometry's own pick, a resolvable
    # map proposal beating the positional heuristic — per aligned dungeon
    # whose truth asserts the way in. Calling `select_entrance` here is the
    # point: the metric and geometry can never pick differently.
    entrance_asserted = 0
    entrance_matched = 0
    for record in dungeon_records:
        entrance = record.truth_dungeon.entrance
        if entrance is None:
            continue
        entrance_asserted += 1
        selection = select_entrance(
            record.extracted_dungeon,
            {
                number: reading
                for (dungeon_id, number), reading in readings.items()
                if dungeon_id == record.extracted_dungeon.id
            },
        )
        if selection.level_number is None or record.level_matches.get(entrance.level) != selection.level_number:
            continue
        if _truth_endpoint(record.truth_dungeon, entrance.level, entrance.key)[1] != selection.area_key:
            continue
        pairing = next(pairing for pairing in record.pairings if pairing.truth_level.number == entrance.level)
        if selection.area_key in pairing.matched:
            entrance_matched += 1

    return ModuleMetrics(
        areas=AreaMetrics(
            truth_dungeons=len(truth.dungeons),
            extracted_dungeons=len(index.dungeons),
            matched_dungeons=len(matches),
            truth_areas=truth_area_count,
            extracted_areas=extracted_area_count,
            matched=matched_areas,
            recall=_ratio(matched_areas, truth_area_count),
            precision=_ratio(matched_areas, extracted_area_count),
        ),
        encounters=EncounterMetrics(
            truth_encounters=truth_encounters,
            name_matched=name_matched,
            name_recall=_ratio(name_matched, truth_encounters),
            precision_denominator=precision_denominator,
            precision_matched=precision_matched,
            precision=_ratio(precision_matched, precision_denominator),
            count_denominator=count_denominator,
            count_matched=count_matched,
            count_accuracy=_ratio(count_matched, count_denominator),
            resolution_denominator=resolution_denominator,
            resolution_matched=resolution_matched,
            resolution_accuracy=_ratio(resolution_matched, resolution_denominator),
            custom_denominator=custom_denominator,
            custom_matched=custom_matched,
            custom_accuracy=_ratio(custom_matched, custom_denominator),
            non_srd=non_srd,
        ),
        connections=ConnectionMetrics(
            truth_edges=len(truth_edges),
            extracted_edges=len(extracted_edges),
            true_positives=true_positives,
            precision=precision,
            recall=recall,
            f1=f1,
        ),
        treasure=TreasureMetrics(
            presence_denominator=presence_denominator,
            presence_matched=presence_matched,
            presence_agreement=_ratio(presence_matched, presence_denominator),
            letters_denominator=letters_denominator,
            letters_matched=letters_matched,
            letter_accuracy=_ratio(letters_matched, letters_denominator),
        ),
        doors=DoorMetrics(
            truth_doors=len(truth_doors),
            extracted_doors=len(extracted_doors),
            true_positives=len(door_pairs),
            recall=_ratio(len(door_pairs), len(truth_doors)),
            precision=_ratio(len(door_pairs), len(extracted_doors)),
            kind_matched=door_kind_matched,
            kind_accuracy=_ratio(door_kind_matched, len(door_pairs)),
            locked_matched=door_locked_matched,
            locked_accuracy=_ratio(door_locked_matched, len(door_pairs)),
        ),
        transitions=TransitionMetrics(
            asserted_dungeons=transition_asserted_dungeons,
            truth_transitions=truth_transition_count,
            extracted_transitions=extracted_transition_count,
            true_positives=transition_true_positives,
            recall=_ratio(transition_true_positives, truth_transition_count),
            precision=_ratio(transition_true_positives, extracted_transition_count),
            kind_matched=transition_kind_matched,
            kind_accuracy=_ratio(transition_kind_matched, transition_true_positives),
        ),
        entrances=EntranceMetrics(
            asserted=entrance_asserted,
            matched=entrance_matched,
            accuracy=_ratio(entrance_matched, entrance_asserted),
        ),
    )


def _module_mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


def corpus_means(scoreboard: Scoreboard) -> dict[str, float | None]:
    """The corpus mean of each headline metric, over modules where it is defined.

    Args:
        scoreboard: The scoreboard to summarize.

    Returns:
        Metric name → mean (None when no module defines it).
    """
    modules = list(scoreboard.modules.values())
    return {
        "area_recall": _module_mean([score.metrics.areas.recall for score in modules]),
        "area_precision": _module_mean([score.metrics.areas.precision for score in modules]),
        "encounter_name_recall": _module_mean([score.metrics.encounters.name_recall for score in modules]),
        "encounter_count_accuracy": _module_mean([score.metrics.encounters.count_accuracy for score in modules]),
        "encounter_resolution_accuracy": _module_mean(
            [score.metrics.encounters.resolution_accuracy for score in modules]
        ),
        "encounter_custom_accuracy": _module_mean([score.metrics.encounters.custom_accuracy for score in modules]),
        "encounter_precision": _module_mean([score.metrics.encounters.precision for score in modules]),
        "connection_f1": _module_mean([score.metrics.connections.f1 for score in modules]),
        "treasure_presence_agreement": _module_mean([score.metrics.treasure.presence_agreement for score in modules]),
        "treasure_letter_accuracy": _module_mean([score.metrics.treasure.letter_accuracy for score in modules]),
        "door_recall": _module_mean([score.metrics.doors.recall for score in modules]),
        "door_precision": _module_mean([score.metrics.doors.precision for score in modules]),
        "door_kind_accuracy": _module_mean([score.metrics.doors.kind_accuracy for score in modules]),
        "door_locked_accuracy": _module_mean([score.metrics.doors.locked_accuracy for score in modules]),
        "transition_recall": _module_mean([score.metrics.transitions.recall for score in modules]),
        "transition_precision": _module_mean([score.metrics.transitions.precision for score in modules]),
        "transition_kind_accuracy": _module_mean([score.metrics.transitions.kind_accuracy for score in modules]),
        "entrance_accuracy": _module_mean([score.metrics.entrances.accuracy for score in modules]),
    }
