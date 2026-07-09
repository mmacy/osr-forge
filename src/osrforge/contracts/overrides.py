"""The overrides contract: the human correction channel, `overrides.yaml`.

Overrides are a human-authored input, not a produced artifact, and carry no
version field in v1 — strict unknown-key rejection means a future revision that
needs one can add it detectably. Every entry carries a required, non-empty
`reason`. Replacement payloads are osrlib's own models embedded directly —
never re-declared — so an override osrlib would reject fails at overrides load
time, not assembly time.

Field semantics, pinned: **absent means untouched; explicit `null` means
clear.** Pydantic's unset-vs-`None` distinction models this exactly — check
`model_fields_set` to tell them apart. Application happens in phase 3.
"""

import re
from pathlib import Path
from typing import Annotated

import yaml
from osrlib.crawl.dungeon import (
    AreaTreasureSpec,
    Edge,
    FeatureSpec,
    KeyedEncounter,
    Position,
    TransitionSpec,
    TrapSpec,
)
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints
from pydantic import Field as PydanticField

from osrforge.contracts.report import AreaAddressString, LevelAddressString

__all__ = [
    "AreaGeometryOverride",
    "AreaOverride",
    "EdgeKeyString",
    "GeometryOverride",
    "ModuleOverride",
    "MonsterOverride",
    "Overrides",
    "TownOverride",
    "load_overrides",
]

Reason = Annotated[str, StringConstraints(min_length=1)]
"""The required justification on every override entry."""

_EDGE_KEY_PATTERN = re.compile(r"^\d+,\d+:(north|south|east|west)$")


def _validate_edge_key(value: str) -> str:
    if not _EDGE_KEY_PATTERN.match(value):
        raise ValueError(f"edge key must be 'x,y:direction' with a compass direction: {value!r}")
    return value


EdgeKeyString = Annotated[str, AfterValidator(_validate_edge_key)]
"""An edge key: `x,y:direction`, any of the four directions.

osrlib's canonical [`edge_key`][osrlib.crawl.dungeon.edge_key] form stores only
`north`/`west` keys (a cell's east edge is its eastern neighbour's west edge);
override keys accept all four directions and application (phase 3)
canonicalizes them.
"""

_NonEmptyKey = Annotated[str, StringConstraints(min_length=1)]


def _validate_area_key(value: str) -> str:
    if "/" in value:
        raise ValueError(f"'/' is not allowed in area keys: {value!r}")
    return value


_AreaKeyString = Annotated[str, StringConstraints(min_length=1), AfterValidator(_validate_area_key)]


class MonsterOverride(BaseModel):
    """Remap one extracted monster name to a catalog template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: Annotated[str, StringConstraints(min_length=1)]
    reason: Reason


class AreaOverride(BaseModel):
    """Replace fields of one keyed area, add an area, or remove one.

    An entry addressing an area the draft doesn't have is an area *add* and must
    carry the full required payload — enforced at application time (phase 3),
    since only assembly knows what the draft contains.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    description: str | None = None
    encounter: KeyedEncounter | None = None
    trap: TrapSpec | None = None
    treasure: AreaTreasureSpec | None = None
    features: tuple[FeatureSpec, ...] | None = None
    remove: bool = False
    reason: Reason


class AreaGeometryOverride(BaseModel):
    """Replace one area's cell cluster."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cells: tuple[Position, ...] = PydanticField(min_length=1)


class GeometryOverride(BaseModel):
    """Correct one level's geometry: area cells, edges, entrance, transitions.

    The nesting follows osrlib's models: a level owns edges, entrance, and
    transitions; an area owns only its cells.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    areas: dict[_AreaKeyString, AreaGeometryOverride] = {}
    edges: dict[EdgeKeyString, Edge] = {}
    entrance: Position | None = None
    transitions: tuple[TransitionSpec, ...] | None = None
    reason: Reason


class TownOverride(BaseModel):
    """Replace base-town metadata fields ([`TownSpec`][osrlib.crawl.adventure.TownSpec] fields)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    description: str | None = None
    services: tuple[str, ...] | None = None
    travel_turns: dict[str, int] | None = None
    reason: Reason


class ModuleOverride(BaseModel):
    """Replace adventure metadata fields: name, description, hooks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = None
    description: str | None = None
    hooks: tuple[str, ...] | None = None
    reason: Reason


class Overrides(BaseModel):
    """The `overrides.yaml` document: the spec's v1 override kinds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    monsters: dict[_NonEmptyKey, MonsterOverride] = {}
    areas: dict[AreaAddressString, AreaOverride] = {}
    geometry: dict[LevelAddressString, GeometryOverride] = {}
    town: TownOverride | None = None
    module: ModuleOverride | None = None


def load_overrides(path: Path) -> Overrides:
    """Load an overrides file, or the empty `Overrides` when it doesn't exist.

    Parsing uses `yaml.safe_load` only; the result is model-validated, so typos
    and payloads osrlib would reject fail here. Known gap, deferred to phase 3's
    application work: pyyaml's default loader silently keeps the last of
    duplicate YAML keys.

    Args:
        path: The `overrides.yaml` path. A missing file is an empty overrides set.

    Returns:
        The validated overrides.

    Raises:
        pydantic.ValidationError: If the document doesn't match the contract.
        yaml.YAMLError: If the file is not valid YAML.
    """
    if not path.exists():
        return Overrides()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return Overrides()
    return Overrides.model_validate(data)
