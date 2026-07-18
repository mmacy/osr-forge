"""Eval scoring: truth-file models, alignment, and the pinned metric families.

This is the pure half of the spec's "ship quality evals so extraction changes
are measured, not vibed": deterministic, CI-tested code that scores a
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
from collections.abc import Collection
from difflib import SequenceMatcher
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrforge.assemble import parse_treasure, usable_stat_block
from osrforge.contracts.stages import (
    AreaContent,
    LevelContent,
    MonsterResolutions,
    StatBlocks,
    SurveyDungeon,
    SurveyIndex,
)
from osrforge.monsters import normalize_monster_name
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
    "EncounterMetrics",
    "ManifestLicense",
    "ModuleMetrics",
    "ModuleScore",
    "ModuleTruth",
    "RunInfo",
    "Scoreboard",
    "TreasureMetrics",
    "TruthArea",
    "TruthDungeon",
    "TruthEncounter",
    "TruthLevel",
    "TruthProvenance",
    "TruthTreasure",
    "corpus_means",
    "enforce_source_integrity",
    "load_byom_scoreboard",
    "load_manifest",
    "load_scoreboard",
    "load_truth",
    "publish_module",
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


class TruthArea(BaseModel):
    """One keyed area, identified by its printed key.

    `connections` and `treasure` are assertion-aware: `None` (omitted) means
    the fact was not asserted — the area's edges are out of the connection
    metric's universe, or the area is outside both treasure denominators. A
    present value asserts the complete fact set: the area's full same-level
    connected printed-key list (possibly empty), or the area's treasure facts.
    Assertion-awareness is what makes time-boxed partial truth honest: a truth
    file covering every area key plus a verified sample of areas still yields
    exact area recall and honestly-denominated treasure agreement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    encounters: tuple[TruthEncounter, ...] = ()
    connections: tuple[str, ...] | None = None
    treasure: TruthTreasure | None = None


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


class TruthDungeon(BaseModel):
    """One printed adventuring site."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    levels: tuple[TruthLevel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _level_numbers_unique(self) -> TruthDungeon:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"truth level numbers must be unique per dungeon: {numbers}")
        return self


class ModuleTruth(BaseModel):
    """A corpus module's verified structural ground truth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeons: tuple[TruthDungeon, ...] = Field(min_length=1)


class ManifestLicense(BaseModel):
    """The license record: SPDX id plus the phase 0 verification note."""

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
    owner's copy with no redistribution surface — the phase 0 verification
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
    """The areas family: recall (the spec's named metric), the hallucination guard, and dungeon alignment.

    The dungeon counts make the survey mode legible in every scoreboard entry
    — phase 4's measured JN1 mode-flip (ten lairs collapsing into one dungeon
    on a re-roll) reads as `truth_dungeons=14, extracted_dungeons=5` instead
    of requiring a trip to `survey.json`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_dungeons: int
    extracted_dungeons: int
    matched_dungeons: int
    truth_areas: int
    extracted_areas: int
    matched: int
    recall: float | None
    precision: float | None


class EncounterMetrics(BaseModel):
    """The encounters family: name recall, count accuracy, resolution accuracy, custom-emission accuracy.

    The custom pair scores the truth's `custom: true` assertions against the
    stat-block cache, so emission is its own legible number rather than
    diluting SRD-resolution accuracy; `non_srd` keeps meaning "no SRD
    template and no assertion about emission."
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_encounters: int
    name_matched: int
    name_recall: float | None
    count_denominator: int
    count_matched: int
    count_accuracy: float | None
    resolution_denominator: int
    resolution_matched: int
    resolution_accuracy: float | None
    custom_denominator: int = 0
    custom_matched: int = 0
    custom_accuracy: float | None = None
    non_srd: int


class ConnectionMetrics(BaseModel):
    """The connections family: F1 over undirected same-level edges in the asserted universe."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    truth_edges: int
    extracted_edges: int
    true_positives: int
    precision: float | None
    recall: float | None
    f1: float | None


class TreasureMetrics(BaseModel):
    """The treasure family: presence agreement and letter accuracy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    presence_denominator: int
    presence_matched: int
    presence_agreement: float | None
    letters_denominator: int
    letters_matched: int
    letter_accuracy: float | None


class ModuleMetrics(BaseModel):
    """One module's metrics block: the four pinned families."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    areas: AreaMetrics
    encounters: EncounterMetrics
    connections: ConnectionMetrics
    treasure: TreasureMetrics


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


def score_workdir(workdir_path: Path, truth: ModuleTruth) -> ModuleMetrics:
    """Score one converted workdir's stage caches against a module's truth.

    Reads `stages/survey.json` (area recall/precision), the
    `stages/areas.*.json` content caches (encounters, connections, treasure),
    `stages/monsters.json` (resolution accuracy), and `stages/statblocks.json`
    (custom-emission accuracy — a missing file scores no matches, the honest
    pre-phase-7 state, never an error). Deterministic: scoring the same
    workdir twice yields byte-identical metrics.

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

    Args:
        workdir_path: A workdir whose extraction stages have completed.
        truth: The module's ground truth.

    Returns:
        The four metric families.

    Raises:
        ValueError: If a required stage cache is missing.
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

    matches = _align_dungeons(truth, index)

    truth_area_count = sum(len(level.areas) for dungeon in truth.dungeons for level in dungeon.levels)
    extracted_area_count = sum(len(level.areas) for dungeon in index.dungeons for level in dungeon.levels)
    matched_areas = 0

    truth_encounters = 0
    name_matched = 0
    count_denominator = 0
    count_matched = 0
    resolution_denominator = 0
    resolution_matched = 0
    custom_denominator = 0
    custom_matched = 0
    non_srd = 0

    truth_edges: set[tuple[str, str, int, frozenset[str]]] = set()
    extracted_edges: set[tuple[str, str, int, frozenset[str]]] = set()

    presence_denominator = 0
    presence_matched = 0
    letters_denominator = 0
    letters_matched = 0

    for truth_position, truth_dungeon in enumerate(truth.dungeons):
        for level in truth_dungeon.levels:
            truth_encounters += sum(len(area.encounters) for area in level.areas)

        extracted_position = matches.get(truth_position)
        if extracted_position is None:
            continue
        extracted_dungeon = index.dungeons[extracted_position]
        level_matches = _align_levels(truth_dungeon, extracted_dungeon)
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
                for truth_encounter in truth_area.encounters:
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

            # Connections: undirected same-level edges between matched areas,
            # in the asserted universe (at least one endpoint's neighbor set
            # asserted — an asserted area's list is complete, so any extracted
            # edge incident to it is scoreable).
            level_id = (truth_dungeon.name, extracted_dungeon.id, level.number)
            for position, truth_area in enumerate(level.areas, start=1):
                if truth_area.connections is None:
                    continue
                slug = _truth_key_slug(truth_area, position)
                if slug not in matched:
                    continue
                for neighbor_key in truth_area.connections:
                    neighbor = canonical_slug(neighbor_key)
                    if neighbor in matched and neighbor != slug:
                        truth_edges.add((*level_id, frozenset({slug, neighbor})))
            for extracted_area in cache.areas:
                if extracted_area.key not in matched:
                    continue
                for connection in extracted_area.connections:
                    if connection.to_key is None:
                        # Level-targeted links are outside the same-level edge
                        # universe; edge semantics and denominators untouched.
                        continue
                    to_key = canonical_slug(connection.to_key)
                    if to_key not in matched or to_key == extracted_area.key:
                        continue
                    if extracted_area.key in asserted or to_key in asserted:
                        extracted_edges.add((*level_id, frozenset({extracted_area.key, to_key})))

    true_positives = len(truth_edges & extracted_edges)
    precision = _ratio(true_positives, len(extracted_edges))
    recall = _ratio(true_positives, len(truth_edges))
    f1: float | None = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    elif precision is not None and recall is not None:
        f1 = 0.0

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
        "connection_f1": _module_mean([score.metrics.connections.f1 for score in modules]),
        "treasure_presence_agreement": _module_mean([score.metrics.treasure.presence_agreement for score in modules]),
        "treasure_letter_accuracy": _module_mean([score.metrics.treasure.letter_accuracy for score in modules]),
    }
