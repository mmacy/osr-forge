"""The extraction report: the `report.json` contract.

The report is regenerated on every assembly and is the complete input a review
UI needs. Nothing produces one until phase 2 — this module ships the wire
format so consumers and tests can pin it early.
"""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

from osrforge.contracts.run import TokenUsage
from osrforge.versioning import SCHEMA_VERSION, osrforge_version

__all__ = [
    "AreaAddress",
    "AreaAddressString",
    "AreaReport",
    "ExtractionReport",
    "Flag",
    "FlagString",
    "LevelAddress",
    "LevelAddressString",
    "LintCheck",
    "LintFinding",
    "ModuleInfo",
    "MonsterSummary",
    "ValidationResult",
    "format_flag",
    "parse_flag",
]


class Flag(StrEnum):
    """The report's enumerated flag vocabulary, exactly the spec's — UIs badge on these."""

    GEOMETRY_SYNTHESIZED = "geometry_synthesized"
    MONSTER_UNRESOLVED = "monster_unresolved"
    LOW_CONFIDENCE = "low_confidence"
    CONNECTION_AMBIGUOUS = "connection_ambiguous"
    TREASURE_UNPARSED = "treasure_unparsed"
    PAGE_UNREADABLE = "page_unreadable"


def parse_flag(value: str) -> tuple[Flag, str | None]:
    """Split a serialized flag string into its flag and optional detail.

    Serialized flags are plain strings shaped `<flag>` or `<flag>:<detail>` —
    e.g. `monster_unresolved:hobgoblin chieftain`. The prefix must be a
    [`Flag`][osrforge.contracts.report.Flag] member; the detail is free text and
    may itself contain colons.

    Args:
        value: The serialized flag string.

    Returns:
        The flag and its detail (`None` when the string is a bare flag).

    Raises:
        ValueError: If the prefix is not a known flag or the detail is empty.
    """
    prefix, separator, detail = value.partition(":")
    try:
        flag = Flag(prefix)
    except ValueError:
        raise ValueError(f"unknown flag prefix {prefix!r} in {value!r}") from None
    if separator and not detail:
        raise ValueError(f"flag {value!r} has a colon but no detail")
    return flag, detail if separator else None


def format_flag(flag: Flag, detail: str | None = None) -> str:
    """Serialize a flag and optional detail into the report's string form.

    Args:
        flag: The flag.
        detail: Optional free-text detail. Must be non-empty when given.

    Returns:
        `<flag>` or `<flag>:<detail>`.

    Raises:
        ValueError: If `detail` is an empty string.
    """
    if detail is None:
        return flag.value
    if not detail:
        raise ValueError("flag detail must be non-empty when given")
    return f"{flag.value}:{detail}"


def _validate_flag_string(value: str) -> str:
    parse_flag(value)
    return value


FlagString = Annotated[str, AfterValidator(_validate_flag_string)]
"""A serialized report flag: `<flag>` or `<flag>:<detail>`, prefix a [`Flag`][osrforge.contracts.report.Flag] member."""


def _no_slash(value: str) -> str:
    if not value:
        raise ValueError("address components must be non-empty")
    if "/" in value:
        raise ValueError(f"'/' is not allowed in address components: {value!r}")
    return value


# ASCII digits without a leading zero: address strings key override entries, so
# every level number must have exactly one spelling ("01" or a Unicode digit
# would alias "1" as a distinct key in a human-edited file).
_LEVEL_NUMBER_PATTERN = re.compile(r"^[1-9][0-9]*$")


class AreaAddress(BaseModel):
    """A keyed area's address: `<dungeon-id>/<level-number>/<area-key>`.

    osrlib allows any string id, so the address grammar is only unambiguous
    because osr-forge constrains what it emits: `/` is forbidden in dungeon ids
    and area keys (phase 1's extraction normalization enforces it at the source).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeon_id: str
    level_number: int = Field(ge=1)
    area_key: str

    @field_validator("dungeon_id", "area_key")
    @classmethod
    def _components_slash_free(cls, value: str) -> str:
        return _no_slash(value)

    @classmethod
    def parse(cls, value: str) -> AreaAddress:
        """Parse the `<dungeon-id>/<level-number>/<area-key>` form.

        Args:
            value: The address string.

        Returns:
            The parsed address.

        Raises:
            ValueError: If the string is not three non-empty `/`-separated parts
                with an integer level number of at least 1.
        """
        parts = value.split("/")
        if len(parts) != 3:
            raise ValueError(f"area address must be <dungeon-id>/<level-number>/<area-key>: {value!r}")
        dungeon_id, level, area_key = parts
        if not _LEVEL_NUMBER_PATTERN.match(level):
            raise ValueError(f"level number must be a positive integer without leading zeros: {value!r}")
        return cls(dungeon_id=dungeon_id, level_number=int(level), area_key=area_key)

    def __str__(self) -> str:
        return f"{self.dungeon_id}/{self.level_number}/{self.area_key}"


class LevelAddress(BaseModel):
    """A dungeon level's address: `<dungeon-id>/<level-number>` (geometry overrides)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dungeon_id: str
    level_number: int = Field(ge=1)

    @field_validator("dungeon_id")
    @classmethod
    def _components_slash_free(cls, value: str) -> str:
        return _no_slash(value)

    @classmethod
    def parse(cls, value: str) -> LevelAddress:
        """Parse the `<dungeon-id>/<level-number>` form.

        Args:
            value: The address string.

        Returns:
            The parsed address.

        Raises:
            ValueError: If the string is not two non-empty `/`-separated parts
                with an integer level number of at least 1.
        """
        parts = value.split("/")
        if len(parts) != 2:
            raise ValueError(f"level address must be <dungeon-id>/<level-number>: {value!r}")
        dungeon_id, level = parts
        if not _LEVEL_NUMBER_PATTERN.match(level):
            raise ValueError(f"level number must be a positive integer without leading zeros: {value!r}")
        return cls(dungeon_id=dungeon_id, level_number=int(level))

    def __str__(self) -> str:
        return f"{self.dungeon_id}/{self.level_number}"


def _validate_area_address_string(value: str) -> str:
    AreaAddress.parse(value)
    return value


AreaAddressString = Annotated[str, AfterValidator(_validate_area_address_string)]
"""An area address in its serialized `<dungeon-id>/<level-number>/<area-key>` form."""


def _validate_level_address_string(value: str) -> str:
    LevelAddress.parse(value)
    return value


LevelAddressString = Annotated[str, AfterValidator(_validate_level_address_string)]
"""A level address in its serialized `<dungeon-id>/<level-number>` form."""


class LintCheck(StrEnum):
    """The playability lint's finding ids.

    A published vocabulary UIs badge on, like
    [`Flag`][osrforge.contracts.report.Flag]: growing it is additive, renaming
    a member is a schema-version event.
    """

    EDGE_INVALID = "edge_invalid"
    AREA_UNREACHABLE = "area_unreachable"
    ORPHAN_CELL = "orphan_cell"
    SECRET_ONLY_ACCESS = "secret_only_access"
    TRANSITION_UNPAIRED = "transition_unpaired"
    DELVE_BLOCKED = "delve_blocked"
    DELVE_INCOMPLETE = "delve_incomplete"


class LintFinding(BaseModel):
    """One structured playability finding, as merged into `report.json` by `check`.

    Severity is a field rather than a function of the id: the id→severity table
    is the producer's pin (`check`), so the contract needn't change if a check's
    severity is ever re-judged.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: LintCheck
    severity: Literal["error", "warning"]
    location: str
    message: str


class ModuleInfo(BaseModel):
    """The source module's identity in the report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    pages: int = Field(ge=0)


class ValidationResult(BaseModel):
    """The `validate_adventure` outcome: a draft is allowed to be invalid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    errors: tuple[str, ...] = ()


class AreaReport(BaseModel):
    """One keyed area's extraction record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AreaAddressString
    source_pages: tuple[int, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    flags: tuple[FlagString, ...] = ()
    overridden: tuple[str, ...] = ()


class MonsterSummary(BaseModel):
    """The monster-resolution summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolved: int = Field(ge=0)
    unresolved: tuple[str, ...] = ()


class ExtractionReport(BaseModel):
    """The `report.json` document, mirroring the spec's example.

    `flags` carries module-scope conditions with no per-area home — a defaulted
    adventure title or town name — in the same flag grammar as per-area flags.
    `findings` is empty from `assemble()` (stale lint about a changed draft is
    worse than none; re-assembly wipes findings by design) and populated by
    `check()`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    osrforge_version: str = Field(default_factory=osrforge_version)
    module: ModuleInfo
    validation: ValidationResult
    areas: tuple[AreaReport, ...] = ()
    monsters: MonsterSummary
    usage: TokenUsage
    flags: tuple[FlagString, ...] = ()
    findings: tuple[LintFinding, ...] = ()
