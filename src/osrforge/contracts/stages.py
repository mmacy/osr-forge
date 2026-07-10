"""Stage-cache contracts: the `stages/survey.json`, `areas.<dungeon>.<level>.json`, and `monsters.json` wire formats.

Stage caches are cross-phase wire formats — content reads survey's cache, and
the monsters and assemble stages read both — so their models live here,
in the established home for anything serialized between phases. No stage module
ever imports another stage module.

Pinned reading (mirrors the phase 1 plan): the spec's "cached raw stage
outputs — the LLM's actual answers" is read as the extraction *stage's*
validated-and-normalized output. Canonical ids and keys are normalized at the
source (the survey stage), with the model's original spellings preserved in
`source_label`/`name`; a host that wants the literal wire answers wraps its
provider in `RecordingProvider`.

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
    "AREA_KINDS",
    "CANONICAL_SLUG_PATTERN",
    "DICE_PATTERN",
    "DIRECTIONS",
    "AreaConnection",
    "AreaContent",
    "AreaEncounter",
    "AreaKind",
    "Direction",
    "LevelContent",
    "MonsterResolution",
    "MonsterResolutions",
    "ResolutionMethod",
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
at phase 2.
"""

AreaKind = Literal["room", "corridor", "cave", "landmark", "other"]
"""A surveyed area's rough kind."""

AREA_KINDS: tuple[str, ...] = get_args(AreaKind)
"""The `AreaKind` wire values, for building extraction-schema enums."""

Direction = Literal["north", "south", "east", "west", "up", "down", "unknown"]
"""A connection's compass or vertical direction."""

DIRECTIONS: tuple[str, ...] = get_args(Direction)
"""The `Direction` wire values, for building extraction-schema enums."""


def _canonical(value: str) -> str:
    if not CANONICAL_SLUG_PATTERN.match(value):
        raise ValueError(f"not a canonical slug ([a-z0-9]+ groups joined by single hyphens): {value!r}")
    return value


class TownInfo(BaseModel):
    """The town or home base — never a dungeon.

    `name` may be empty when the town is genuinely unnamed; osrlib's required
    `TownSpec.name` gets a default-plus-flag at assembly (phase 2).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str


class SurveyArea(BaseModel):
    """One keyed area in the survey index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    source_label: str | None = None
    kind: AreaKind
    source_pages: tuple[int, ...]

    @field_validator("key")
    @classmethod
    def _key_canonical(cls, value: str) -> str:
        return _canonical(value)


class SurveyLevel(BaseModel):
    """One dungeon level in the survey index.

    `map_pages` — the pages showing this level's map — is load-bearing for the
    content stage's direction extraction: the map pages ride along on every
    content batch so the model can answer `direction` when the prose is silent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    map_pages: tuple[int, ...]
    areas: tuple[SurveyArea, ...]

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
    name: str
    levels: tuple[SurveyLevel, ...] = Field(min_length=1)

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
    title: str
    hooks: tuple[str, ...]
    town: TownInfo
    dungeons: tuple[SurveyDungeon, ...]
    monster_names: tuple[str, ...]


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
    count_fixed: int | None = Field(default=None, ge=1)
    count_dice: str | None = None
    count_note: str | None = None


class AreaConnection(BaseModel):
    """One extracted connection to another area.

    `to_key` is a free string — connections may cross batches or levels; the
    prompt instructs canonical keys from the survey excerpt, and dangling
    references are assembly's job (phase 2), surfacing as `connection_ambiguous`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    to_key: str
    direction: Direction


class AreaContent(BaseModel):
    """One keyed area's extracted content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    description: str
    encounters: tuple[AreaEncounter, ...]
    trap: str | None = None
    treasure: tuple[str, ...]
    features: tuple[str, ...]
    connections: tuple[AreaConnection, ...]
    source_pages: tuple[int, ...]
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("key")
    @classmethod
    def _key_canonical(cls, value: str) -> str:
        return _canonical(value)


class LevelContent(BaseModel):
    """The `stages/areas.<dungeon>.<level>.json` cache: one level's extracted areas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    dungeon_id: str
    level_number: int = Field(ge=1)
    areas: tuple[AreaContent, ...]

    @field_validator("dungeon_id")
    @classmethod
    def _id_canonical(cls, value: str) -> str:
        return _canonical(value)


ResolutionMethod = Literal["exact", "alias", "fuzzy", "llm", "unresolved"]
"""How a monster name resolved: one of the spec's four tiers, or not at all."""


class MonsterResolution(BaseModel):
    """One extracted name's resolution against the osrlib monster catalog."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str | None = None
    method: ResolutionMethod

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
    `"zombies"`, one resolution must serve both, and phase 3's override matching
    (`overrides.yaml` `monsters:` keys) normalizes the same way.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    resolutions: dict[str, MonsterResolution]

    @field_validator("resolutions")
    @classmethod
    def _keys_sorted(cls, value: dict[str, MonsterResolution]) -> dict[str, MonsterResolution]:
        return dict(sorted(value.items()))
