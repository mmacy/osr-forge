"""Stage-cache contracts: the `stages/survey.json`, `areas.<dungeon>.<level>.json`, and `monsters.json` wire formats.

Stage caches are cross-stage wire formats — content reads survey's cache, and
the monsters and assemble stages read both — so their models live here,
in the established home for anything serialized between stages. No stage module
ever imports another stage module.

Pinned reading: a stage cache holds the extraction *stage's*
validated-and-normalized output, not the model's literal wire answers.
Canonical ids and keys are normalized at the source (the survey stage), with
the model's original spellings preserved in `source_label`/`name`; a host
that wants the literal wire answers wraps its provider in
[`RecordingProvider`][osrforge.providers.fixtures.RecordingProvider].

Caches carry `schema_version` only, not `osrforge_version` — the producing
package version is already recorded once per workdir in `run.json`, and
duplicating it per cache would break golden-file byte-equality on every package
version bump. Usage and model identity likewise live only in `run.json`:
usage varies with provider retries, so putting it here would let byte-identical
extractions produce differing caches.
"""

import re
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from osrforge.versioning import SCHEMA_VERSION

__all__ = [
    "AC_NOTATIONS",
    "AREA_KINDS",
    "CANONICAL_SLUG_PATTERN",
    "CONNECTION_VIAS",
    "DICE_PATTERN",
    "DIRECTIONS",
    "AcNotation",
    "AreaConnection",
    "AreaContent",
    "AreaEncounter",
    "AreaKind",
    "ConnectionVia",
    "Direction",
    "LevelContent",
    "MonsterResolution",
    "MonsterResolutions",
    "RawStatBlock",
    "ResolutionMethod",
    "StatBlocks",
    "SurveyArea",
    "SurveyDungeon",
    "SurveyIndex",
    "SurveyLevel",
    "TownInfo",
]

CANONICAL_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
"""The canonical id/key grammar.

The alphabet is the point: no `/` (the `AreaAddress` grammar forbids it), no
`.` (so `areas.<dungeon>.<level>.json` filenames parse unambiguously and
request tags stay within the tag charset), and lowercase (so a hand-edited
`overrides.yaml` can't alias `4a` against `4A`).
"""

DICE_PATTERN = r"^([1-9][0-9]{0,2})?d(2|3|4|6|8|10|12|20|100)([+-](0|[1-9][0-9]{0,5}))?$"
"""osrlib's dice grammar: optional 1-999 count, the allowed die sizes, optional canonical modifier.

The content-batch schema constrains `count_dice` with this exact pattern, so a
schema-valid dice string is an osrlib-parseable dice string — a looser pattern
like `2d7` or `1d6+07` would pass the cache and fail `KeyedMonster` validation
at assembly.
"""

AreaKind = Literal["room", "corridor", "cave", "landmark", "other"]
"""A surveyed area's rough kind."""

AREA_KINDS: tuple[str, ...] = get_args(AreaKind)
"""The `AreaKind` wire values, for building extraction-schema enums."""

Direction = Literal["north", "south", "east", "west", "up", "down", "unknown"]
"""A connection's compass or vertical direction."""

DIRECTIONS: tuple[str, ...] = get_args(Direction)
"""The `Direction` wire values, for building extraction-schema enums."""

ConnectionVia = Literal["passage", "door", "secret_door", "stairs", "trapdoor", "chute", "other"]
"""A connection's stated mechanism; `passage` when the text names none.

`secret_door` is its own value, not a modifier: it drives both
`DoorSpec(kind="secret")` and the playability lint's `secret_only_access`
warning.
"""

CONNECTION_VIAS: tuple[str, ...] = get_args(ConnectionVia)
"""The `ConnectionVia` wire values, for building extraction-schema enums."""


def _canonical(value: str) -> str:
    if not CANONICAL_SLUG_PATTERN.match(value):
        raise ValueError(f"not a canonical slug ([a-z0-9]+ groups joined by single hyphens): {value!r}")
    return value


class TownInfo(BaseModel):
    """The town or home base — never a dungeon."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    """The printed town name; empty when the town is genuinely unnamed
    (osrlib's required `TownSpec.name` gets a default-plus-flag at assembly)."""

    description: str
    """The module's own description of the town, as extracted."""

    services: tuple[str, ...] = ()
    """The named establishments and services the module states — defaulted so
    survey caches recorded before the field existed still load and assemble."""


class SurveyArea(BaseModel):
    """One keyed area in the survey index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    """The [canonical][canonical-slug] area key, unique within its level."""

    name: str
    """The area's printed name, untouched."""

    source_label: str | None = None
    """The model's original key spelling, preserved wherever the canonical
    `key` differs from a non-empty printed spelling; `None` when they agree
    or the model's spelling was empty."""

    kind: AreaKind
    """The area's rough kind."""

    source_pages: tuple[int, ...]
    """The 1-based source pages the area appears on."""

    @field_validator("key")
    @classmethod
    def _key_canonical(cls, value: str) -> str:
        return _canonical(value)


class SurveyLevel(BaseModel):
    """One dungeon level in the survey index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    """The 1-based level number, unique within its dungeon."""

    map_pages: tuple[int, ...]
    """The pages showing this level's map — load-bearing for the content
    stage's direction extraction: the map pages ride along on every content
    batch so the model can answer `direction` when the prose is silent."""

    areas: tuple[SurveyArea, ...]
    """The level's keyed areas, in survey order."""

    @model_validator(mode="after")
    def _keys_unique(self) -> SurveyLevel:
        keys = [area.key for area in self.areas]
        if len(set(keys)) != len(keys):
            raise ValueError(f"area keys must be unique per level: {keys}")
        return self


class SurveyDungeon(BaseModel):
    """One dungeon in the survey index.

    `id` is the canonical slug derived from `name`; `name` is the model's
    printed name, untouched. There is no `source_label` here — the id derives
    from `name`, which is preserved on the same model, so a label could never
    carry information `name` doesn't.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """The [canonical][canonical-slug] dungeon id, slugged from `name`."""

    name: str
    """The dungeon's printed name, untouched."""

    levels: tuple[SurveyLevel, ...] = Field(min_length=1)
    """The dungeon's levels — at least one."""

    @field_validator("id")
    @classmethod
    def _id_canonical(cls, value: str) -> str:
        return _canonical(value)

    @model_validator(mode="after")
    def _level_numbers_unique(self) -> SurveyDungeon:
        numbers = [level.number for level in self.levels]
        if len(set(numbers)) != len(numbers):
            raise ValueError(f"level numbers must be unique per dungeon: {numbers}")
        return self


class SurveyIndex(BaseModel):
    """The `stages/survey.json` cache: the index that plans everything downstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    """The stage-cache schema version this cache was written under."""

    title: str
    """The module's printed title; empty when unstated (assembly defaults and
    flags it)."""

    description: str = ""
    """The module's own pitch — an excerpt of its printed introduction or
    back-cover text, never invented, empty when the module has none —
    defaulted so survey caches recorded before the field existed still load
    and assemble."""

    hooks: tuple[str, ...]
    """The module's stated adventure hooks."""

    town: TownInfo
    """The town or home base."""

    dungeons: tuple[SurveyDungeon, ...]
    """The surveyed dungeons, in document order."""

    monster_names: tuple[str, ...]
    """The document-wide monster-name superset — wandering tables and
    townsfolk included. The narrower resolution population is
    [`encounter_names`][osrforge.monsters.encounter_names]."""


class AreaEncounter(BaseModel):
    """One extracted encounter: a monster name plus what the module said about count.

    The three count fields are independent optionals; the cache stores what the
    model said. Assembly's encounter builder owns the mapping onto osrlib's
    exactly-one-of rule (prefer dice when both are set; flag when neither is)
    and discards in memory any `count_dice` osrlib's dice parser still rejects,
    flagging the area — defense in depth behind the extraction schema's
    [`DICE_PATTERN`][osrforge.contracts.stages.DICE_PATTERN]. The mapping lives
    in assembly, not the monsters stage: counts are per-encounter facts, and the
    monsters cache is keyed per-name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    monster: str
    """The monster name as extracted."""

    count_fixed: int | None = Field(default=None, ge=1)
    """A stated fixed count (`"3 orcs"`)."""

    count_dice: str | None = None
    """A stated dice count (`"1d6 goblins"`), within
    [`DICE_PATTERN`][osrforge.contracts.stages.DICE_PATTERN]."""

    count_note: str | None = None
    """A stated non-numeric count (`"one per character"`), verbatim."""


class AreaConnection(BaseModel):
    """One extracted connection to another area.

    `to_key` is a free string — connections may cross batches or levels; the
    prompt instructs canonical keys from the survey excerpt, and dangling
    references are assembly's job, surfacing as `connection_ambiguous`.
    `to_level` is the escape hatch for level-shaped targets ("stairs descend to
    the second level" states a level, not a keyed area); the prompt prefers the
    keyed target.

    The failure posture, pinned: tolerate and flag, never reject. The batch
    JSON schema stays flat (no conditional coupling — structured-output
    implementations handle it badly), so a schema-valid response can carry
    door conditions on a non-door `via`, or neither target. This model accepts
    all of it and consumers discard-with-flag: geometry reads door conditions
    only when `via` is a door kind, and a connection with neither `to_key` nor
    `to_level` is skipped with `connection_ambiguous:no target stated`. A
    pydantic error mid-stage would be a crash, not defense.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    to_key: str | None = None
    """The stated target area key; `None` when no keyed target was stated."""

    direction: Direction
    """The stated compass or vertical direction; `unknown` when unstated."""

    via: ConnectionVia = "passage"
    """The stated mechanism; `passage` when the text names none."""

    door_stuck: bool = False
    """A stated stuck-door condition; meaningful only on a door `via`."""

    door_locked: bool = False
    """A stated locked-door condition; meaningful only on a door `via`."""

    to_level: int | None = None
    """A stated target level, for level-shaped targets with no keyed area."""


class AreaContent(BaseModel):
    """One keyed area's extracted content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    """The [canonical][canonical-slug] area key, matching the survey index."""

    description: str
    """The area's extracted keyed description."""

    encounters: tuple[AreaEncounter, ...]
    """The area's extracted encounters."""

    trap: str | None = None
    """The stated trap, verbatim; `None` when the area states none."""

    treasure: tuple[str, ...]
    """The stated treasure strings, verbatim — parsed later by assembly's
    treasure grammar ([`parse_treasure`][osrforge.assemble.parse_treasure])."""

    features: tuple[str, ...]
    """Notable stated features, one entry per feature."""

    connections: tuple[AreaConnection, ...]
    """The stated connections out of this area."""

    source_pages: tuple[int, ...]
    """The 1-based source pages this content was extracted from."""

    confidence: float = Field(ge=0.0, le=1.0)
    """The model's self-assessed extraction confidence for this area."""

    @field_validator("key")
    @classmethod
    def _key_canonical(cls, value: str) -> str:
        return _canonical(value)


class LevelContent(BaseModel):
    """The `stages/areas.<dungeon>.<level>.json` cache: one level's extracted areas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    """The stage-cache schema version this cache was written under."""

    dungeon_id: str
    """The [canonical][canonical-slug] dungeon id, matching the survey index."""

    level_number: int = Field(ge=1)
    """The 1-based level number, matching the survey index."""

    areas: tuple[AreaContent, ...]
    """The level's extracted areas, in survey order."""

    @field_validator("dungeon_id")
    @classmethod
    def _id_canonical(cls, value: str) -> str:
        return _canonical(value)


ResolutionMethod = Literal["exact", "alias", "fuzzy", "llm", "unresolved", "override", "custom"]
"""How a name resolved: one of the four [resolution tiers][resolution-tiers], not at all, a human override, or emission.

`override` and `custom` appear only in memory, when a monster override
supersedes a cached resolution or template emission gives an unresolved name
the module's own creature during assembly — the `monsters.json` cache is
written by the monsters stage alone and never contains either.
"""


class MonsterResolution(BaseModel):
    """One extracted name's resolution against the osrlib monster catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str | None = None
    """The matched catalog template id; `None` exactly when unresolved."""

    method: ResolutionMethod
    """Which tier produced the match (or `unresolved`)."""

    @model_validator(mode="after")
    def _template_iff_resolved(self) -> MonsterResolution:
        if (self.template_id is None) != (self.method == "unresolved"):
            raise ValueError("template_id must be set exactly when the method is not 'unresolved'")
        return self


class MonsterResolutions(BaseModel):
    """The `stages/monsters.json` cache: every keyed encounter name's resolution.

    Keys are **normalized names** (casefolded, internal whitespace collapsed,
    stripped — `normalize_monster_name`), sorted ascending for byte stability.
    Normalization is the point: modules spell the same monster `"Zombies"` and
    `"zombies"`, one resolution must serve both, and override matching
    (`overrides.yaml` `monsters:` keys) normalizes the same way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    """The stage-cache schema version this cache was written under."""

    resolutions: dict[str, MonsterResolution]
    """Normalized name → its resolution, keys sorted ascending."""

    @field_validator("resolutions")
    @classmethod
    def _keys_sorted(cls, value: dict[str, MonsterResolution]) -> dict[str, MonsterResolution]:
        return dict(sorted(value.items()))


AcNotation = Literal["descending", "ascending", "dual"]
"""How a printed armour class counts: classic descending, modern ascending, or both (`5 [14]`)."""

AC_NOTATIONS: tuple[str, ...] = get_args(AcNotation)
"""The `AcNotation` wire values, for building extraction-schema enums."""


class RawStatBlock(BaseModel):
    """One creature's printed stat block, transcribed system-neutrally — never converted.

    Every field is the page's text or number as printed; the stat-block pass
    transcribes and classifies notation, nothing more — every rules judgment
    (AC complements, THAC0/saves/XP derivation, movement rates) lives in
    assembly's deterministic mapping, where it is testable and correctable.
    A value the pages don't print is `None`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ac: str | None = None
    """The armour-class value exactly as printed (`"5"`, `"5 [14]"`)."""

    ac_notation: AcNotation | None = None
    """Which system the printed `ac` counts in."""

    thac0: str | None = None
    """The printed to-hit line, keeping its notation (`"17"`, `"19 [+0]"`, `"+2"`)."""

    hit_dice: str | None = None
    """The Hit Dice line as printed (`"3+1"`, `"1-1"`, `"½"`, `"2d8"`)."""

    class_level: str | None = None
    """A printed class-and-level designation (`"F 3"`, `"3rd-level cleric"`) —
    the leveled-NPC shape that prints no HD line; `hit_dice` and this are the
    two printed forms of the same fact."""

    hp: int | None = Field(default=None, ge=1)
    """The printed hit points."""

    attacks: tuple[str, ...] = ()
    """One entry per printed attack line, counts and damage as printed
    (`"2 claws (1d4 each)"`)."""

    movement: str | None = None
    """The printed movement line (`"120' (40')"`, `"Fly 180' (60')"`)."""

    saves: str | None = None
    """The printed saving-throw line, whatever its form (`"D12 W13 P14 B15
    S16 (2)"`, `"save as F2"`)."""

    morale: int | None = Field(default=None, ge=2, le=12)
    """The printed morale score."""

    alignment: str | None = None
    """The printed alignment, verbatim."""

    xp: int | None = Field(default=None, ge=0)
    """The printed XP award."""

    number_appearing: str | None = None
    """The printed number-appearing value (`"1d6 (2d6)"`, `"2-8"`)."""

    special: tuple[str, ...] = ()
    """One entry per printed special-ability line or note."""

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """The transcription self-assessment. Defaults to 1.0 because an
    override-supplied block is the human's word; the model pass always sets
    its own."""

    source_pages: tuple[int, ...] = ()
    """The request's page numbers the block was read from."""


class StatBlocks(BaseModel):
    """The `stages/statblocks.json` cache: raw printed stat blocks for the unresolved names.

    Assembly is driven purely by this cache's contents — it never reads the
    `custom_monsters` knob itself, only the echo stored here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    """The stage-cache schema version this cache was written under."""

    custom_monsters: Literal["emit", "off"]
    """The knob the stage ran under, echoed."""

    blocks: dict[str, RawStatBlock | None] = {}
    """Normalized name → its raw block, keys sorted ascending. Under `emit`,
    an entry for *every* name the resolution tiers left unresolved — a block,
    or an explicit `null` absent marker (the pass ran and found nothing).
    Under `off`, empty."""

    @field_validator("blocks")
    @classmethod
    def _keys_sorted(cls, value: dict[str, RawStatBlock | None]) -> dict[str, RawStatBlock | None]:
        return dict(sorted(value.items()))
