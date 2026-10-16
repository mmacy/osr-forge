"""What a synthetic map is made of before its answer is worked out.

A [`MapSpec`][osrforge.tracing.synthetic._spec.MapSpec] holds everything a map's
author decides: the dungeon and its survey, each region's plan and style, how
regions form panels and panels form levels, the halves, and the damage sites.
The answer is worked out from it by the conventions, never written by hand.
"""

from dataclasses import dataclass
from typing import Literal

from osrforge.contracts.stages import AreaConnection, AreaKind
from osrforge.tracing.synthetic import DamageSite, Feature
from osrforge.tracing.synthetic._plan import Plan
from osrforge.tracing.truth import HalfPair, PageSide, Style, SymbolKind

type Cell = tuple[int, int]
type Point = tuple[float, float]


@dataclass(frozen=True)
class AreaSpec:
    """One surveyed area and its prose.

    The prose's connections to the areas it adjoins on the map are worked out
    from the answer. `links` adds the connections the map can't show: stairs
    to another level, or the way from an unprinted key to the area it leads to.
    """

    key: str
    name: str
    description: str
    kind: AreaKind = "room"
    links: tuple[AreaConnection, ...] = ()


@dataclass(frozen=True)
class LevelSpec:
    """One surveyed level: its areas in survey order, and its panels in page order."""

    number: int
    areas: tuple[AreaSpec, ...]
    panels: tuple[str, ...]


@dataclass(frozen=True)
class DungeonSpec:
    """One surveyed dungeon."""

    id: str
    name: str
    levels: tuple[LevelSpec, ...]


@dataclass(frozen=True)
class RegionSpec:
    """One framed drawing on one page's image, with its own plan."""

    id: str
    page: int
    image: str
    style: Style
    plan: Plan
    title: str
    cut_off: tuple[PageSide, ...] = ()
    legend: tuple[SymbolKind, ...] = ()
    """The symbols the legend in force explains: the region's own, or the one it uses from `legend_from`."""

    scale_feet: Literal[5, 10] = 10
    """Feet per square of the region's grid."""

    prints_compass: bool = True
    legend_from: int | None = None
    """The page whose legend the region uses when it prints none, or `None` when it prints its own."""

    fold: Plan | None = None
    """For a half whose image edge cuts through a column: the plan as drawn, `plan` with that column added on the
    fold side. The column belongs to neither half's grid."""

    fold_shown: float = 0.0
    """How much of the column the fold cuts the image shows, in cells."""

    at: Point | None = None
    """For a region drawn on an image that an earlier region is on: where its grid's north-west corner sits, in the
    grid coordinates of the first region on that image."""


@dataclass(frozen=True)
class OutlineSpec:
    """One level's outline on a panel that several levels share, in the panel's cells."""

    level: int
    rects: tuple[tuple[int, int, int, int], ...]
    seeds: tuple[Cell, ...]


@dataclass(frozen=True)
class PanelSpec:
    """One panel: a region, or two joined halves, with the plan of the whole panel."""

    id: str
    regions: tuple[tuple[str, Cell], ...]
    plan: Plan
    outlines: tuple[OutlineSpec, ...] = ()
    """The outlines of the levels that share the panel, or none when one level uses it and `plan` holds its outline."""


@dataclass(frozen=True)
class MapSpec:
    """Everything one synthetic map's author decides."""

    name: str
    title: str
    features: tuple[Feature, ...]
    dungeon: DungeonSpec
    regions: tuple[RegionSpec, ...]
    panels: tuple[PanelSpec, ...]
    half_pairs: tuple[HalfPair, ...] = ()
    entrance_key: str | None = None
    damage: tuple[DamageSite, ...] = ()
