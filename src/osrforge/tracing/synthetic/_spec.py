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
from osrforge.tracing.truth import Compass, HalfPair, Legend, Page, PageSide, Scale, Style, SymbolKind

type Cell = tuple[int, int]

UPRIGHT = Compass(north="top", printed=True)
"""A printed compass pointing to the top of the page."""

TEN_FEET = Scale(feet=10, printed=True, text="1 SQUARE = 10 FEET")
"""The scale every map prints unless it says otherwise."""

PRINTED_LEGEND = Legend(printed=True)
"""A legend the page prints, explaining none of the symbol vocabulary."""


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
    """One surveyed level: its areas in survey order, and its panels in page order.

    A level whose map can't be traced has no panels: `fallback` gives the
    reason it falls back to synthesis, and `pages` its map pages.
    """

    number: int
    areas: tuple[AreaSpec, ...]
    panels: tuple[str, ...]
    fallback: str | None = None
    pages: tuple[int, ...] = ()


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
    compass: Compass = UPRIGHT
    scale: Scale | None = None
    """The printed scale, or `None` for the 10-foot scale every map prints by default."""

    legend_from: int | None = None
    """The page whose legend the region uses when it prints none; `legend` then lists that legend's symbols."""


@dataclass(frozen=True)
class PanelSpec:
    """One panel: a region, or two joined halves, with the plan of the whole panel.

    The plan is in the panel's own squares and in page orientation, as the
    truth format records a panel's reading.
    """

    id: str
    regions: tuple[tuple[str, Cell], ...]
    plan: Plan
    scale_feet: Literal[5, 10] = 10


@dataclass(frozen=True)
class SheetSpec:
    """A picture on a page that isn't a plan the stage traces.

    A sheet with a `verdict` is a region the stage refuses or can't
    register. A sheet without one is a picture no region uses, such as an
    image hidden under another or one on a blanked page. `drawing` names the
    picture in the sheet drawings.
    """

    id: str
    page: int
    image: str
    style: Style
    drawing: str
    title: str
    verdict: Literal["refused", "unregistered"] | None = None
    reason: str | None = None
    columns: int = 0
    rows: int = 0
    scale: Scale = TEN_FEET
    compass: Compass = UPRIGHT
    legend: Legend = PRINTED_LEGEND
    plan: Plan | None = None
    """The plan a `plan` or `warped` drawing draws."""


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
    sheets: tuple[SheetSpec, ...] = ()
    pages: tuple[Page, ...] = ()
    """The page manifest, when it isn't one image per region's page."""
