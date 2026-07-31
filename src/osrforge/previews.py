"""The deterministic SVG level renderer and the previews index page.

Pure string assembly with no dependencies and no timestamps — previews are one
of the three pure artifacts, so [byte-stability][byte-stability] across runs
is a contract. The renderer supports the full osrlib `Edge` model: synthesis
emits `open` and `door` edges, and geometry overrides can introduce anything
the model allows.

Element emission order is pinned (ruler, cells, walls, doors, entrance,
transitions, labels — each group in a deterministic sort) so identical specs
render identical bytes.
"""

import html
from typing import NamedTuple

from osrlib.crawl.dungeon import Direction, EdgeKind, LevelSpec, Position

__all__ = ["CELL_PX", "PreviewIndexLevel", "render_level_svg", "render_previews_index"]

CELL_PX = 24
"""The fixed cell size in pixels."""

_PAD = 12
_TITLE_HEIGHT = 20
_RULER_WIDTH = 18
_RULER_HEIGHT = 12

_AREA_FILL = "#ddd4c0"
_CORRIDOR_FILL = "#efe9dc"
_WALL_STROKE = "#333333"
_DOOR_FILL = "#8b5a2b"
_ENTRANCE_STROKE = "#1a7f37"
_TEXT_FILL = "#333333"
_RULER_FILL = "#888888"

_TRANSITION_GLYPHS = {"stairs_up": "▲", "stairs_down": "▼", "trapdoor": "T", "chute": "C"}


def _row_major(cells: set[Position]) -> list[Position]:
    return sorted(cells, key=lambda cell: (cell[1], cell[0]))


def _cell_origin(cell: Position) -> tuple[int, int]:
    return (_PAD + _RULER_WIDTH + cell[0] * CELL_PX, _PAD + _TITLE_HEIGHT + _RULER_HEIGHT + cell[1] * CELL_PX)


def _wall_segment(cell: Position, direction: Direction) -> tuple[int, int, int, int]:
    x, y = _cell_origin(cell)
    if direction is Direction.NORTH:
        return (x, y, x + CELL_PX, y)
    if direction is Direction.SOUTH:
        return (x, y + CELL_PX, x + CELL_PX, y + CELL_PX)
    if direction is Direction.WEST:
        return (x, y, x, y + CELL_PX)
    return (x + CELL_PX, y, x + CELL_PX, y + CELL_PX)


def _door_tick(cell: Position, direction: Direction, secret: bool) -> str:
    x1, y1, x2, y2 = _wall_segment(cell, direction)
    center_x, center_y = (x1 + x2) // 2, (y1 + y2) // 2
    if y1 == y2:
        rect = f'x="{center_x - 6}" y="{center_y - 3}" width="12" height="6"'
    else:
        rect = f'x="{center_x - 3}" y="{center_y - 6}" width="6" height="12"'
    style = 'fill="none" stroke="#8b5a2b" stroke-width="2"' if secret else f'fill="{_DOOR_FILL}"'
    return f"<rect {rect} {style}/>"


def render_level_svg(dungeon_id: str, level: LevelSpec) -> str:
    """Render one level's grid map as an SVG document string.

    Area cells are filled and labelled with the area id at each area's first
    cell; corridor cells (non-area cells with at least one non-wall edge) get
    a lighter fill; walls are solid strokes wherever the edge map says wall,
    including the implicit boundary; doors are ticks (outlined when secret);
    the entrance is circled; transitions carry their glyph (▲ stairs up,
    ▼ down). A coordinate ruler frames the grid — column numbers across the
    top, row numbers down the left — printing the exact 0-based `x, y` values
    a geometry override's `cells` and edge keys use.

    Args:
        dungeon_id: The dungeon id, for the title line.
        level: The level spec to render.

    Returns:
        The SVG document, ending in a newline.
    """
    area_cells = {cell for area in level.areas for cell in area.cells}
    corridor_cells = {
        (x, y)
        for x in range(level.width)
        for y in range(level.height)
        if (x, y) not in area_cells
        and any(level.edge((x, y), direction).kind is not EdgeKind.WALL for direction in Direction)
    }
    active = area_cells | corridor_cells

    parts: list[str] = []
    title = f"{dungeon_id} level {level.number}"
    # 8 px per character over-estimates a 12 px monospace advance, so the
    # title never clips on a narrow grid.
    width_px = max(_RULER_WIDTH + level.width * CELL_PX, len(title) * 8) + 2 * _PAD
    height_px = level.height * CELL_PX + 2 * _PAD + _TITLE_HEIGHT + _RULER_HEIGHT
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" height="{height_px}" '
        f'viewBox="0 0 {width_px} {height_px}">'
    )
    parts.append(
        f'<text x="{_PAD}" y="{_PAD + 4}" font-family="monospace" font-size="12" fill="{_TEXT_FILL}">{title}</text>'
    )
    grid_left, grid_top = _cell_origin((0, 0))
    for x in range(level.width):
        parts.append(
            f'<text x="{grid_left + x * CELL_PX + CELL_PX // 2}" y="{grid_top - 3}" font-family="monospace" '
            f'font-size="8" text-anchor="middle" fill="{_RULER_FILL}">{x}</text>'
        )
    for y in range(level.height):
        parts.append(
            f'<text x="{grid_left - 4}" y="{grid_top + y * CELL_PX + CELL_PX // 2 + 3}" font-family="monospace" '
            f'font-size="8" text-anchor="end" fill="{_RULER_FILL}">{y}</text>'
        )
    for area in level.areas:
        for cell in area.cells:
            x, y = _cell_origin(cell)
            parts.append(f'<rect x="{x}" y="{y}" width="{CELL_PX}" height="{CELL_PX}" fill="{_AREA_FILL}"/>')
    for cell in _row_major(corridor_cells):
        x, y = _cell_origin(cell)
        parts.append(f'<rect x="{x}" y="{y}" width="{CELL_PX}" height="{CELL_PX}" fill="{_CORRIDOR_FILL}"/>')

    walls: set[tuple[int, int, int, int]] = set()
    doors: list[str] = []
    for cell in _row_major(active):
        for direction in Direction:
            edge = level.edge(cell, direction)
            if edge.kind is EdgeKind.WALL:
                walls.add(_wall_segment(cell, direction))
            elif edge.kind is EdgeKind.DOOR and edge.door is not None:
                tick = _door_tick(cell, direction, secret=edge.door.kind == "secret")
                if tick not in doors:
                    doors.append(tick)
    for x1, y1, x2, y2 in sorted(walls):
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{_WALL_STROKE}" stroke-width="2"/>')
    parts.extend(doors)

    if level.entrance is not None:
        x, y = _cell_origin(level.entrance)
        parts.append(
            f'<circle cx="{x + CELL_PX // 2}" cy="{y + CELL_PX // 2}" r="7" '
            f'fill="none" stroke="{_ENTRANCE_STROKE}" stroke-width="2"/>'
        )
    for transition in level.transitions:
        x, y = _cell_origin(transition.position)
        glyph = _TRANSITION_GLYPHS[transition.kind]
        parts.append(
            f'<text x="{x + CELL_PX // 2}" y="{y + CELL_PX - 6}" font-family="monospace" font-size="12" '
            f'text-anchor="middle" fill="{_TEXT_FILL}">{glyph}</text>'
        )
    for area in level.areas:
        x, y = _cell_origin(area.cells[0])
        # The halo keeps a label legible over the entrance ring or a glyph
        # sharing its cell; paint-order draws the stroke under the fill.
        parts.append(
            f'<text x="{x + 3}" y="{y + 10}" font-family="monospace" font-size="8" '
            f'paint-order="stroke" stroke="{_CORRIDOR_FILL}" stroke-width="2" fill="{_TEXT_FILL}">'
            f"{area.id}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


class PreviewIndexLevel(NamedTuple):
    """One level's row in the previews index: the caller supplies layout-relative hrefs."""

    heading: str
    """The level's section heading (`The Bone Barrow — level 1`)."""

    svg_href: str
    """The level's preview SVG, relative to `index.html`."""

    map_pages: tuple[tuple[int, str], ...]
    """The level's surveyed map pages as `(page number, href)` pairs, relative to `index.html`."""


_INDEX_STYLE = """\
body { font-family: monospace; margin: 1.5rem; background: #efe9dc; color: #333333; }
h1 { font-size: 1.2rem; }
h2 { font-size: 1rem; margin: 2rem 0 0.5rem; }
.pair { display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; }
.pair figure { flex: 1 1 24rem; min-width: 20rem; margin: 0; }
img { width: 100%; height: auto; border: 1px solid #333333; background: #ffffff; }
figcaption { margin-top: 0.25rem; font-size: 0.8rem; }\
"""


def _figure(href: str, caption: str) -> str:
    source = html.escape(href, quote=True)
    label = html.escape(caption)
    return f'<figure><img src="{source}" alt="{label}"><figcaption>{label}</figcaption></figure>'


def render_previews_index(title: str, levels: tuple[PreviewIndexLevel, ...]) -> str:
    """Render `previews/index.html`: each level's synthesized map beside its surveyed map pages.

    The page is the eyeballing surface the correction loop's first step asks
    for — the SVG preview and the module's own page renders side by side, no
    tab-flipping. Pure string assembly like the SVGs: no timestamps, no
    scripts, deterministic bytes.

    Args:
        title: The module title, as built.
        levels: One entry per dungeon level, in survey order.

    Returns:
        The HTML document, ending in a newline.
    """
    heading = html.escape(f"{title} — previews")
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        '<meta charset="utf-8">',
        f"<title>{heading}</title>",
        f"<style>\n{_INDEX_STYLE}\n</style>",
        f"<h1>{heading}</h1>",
    ]
    for level in levels:
        parts.append(f"<h2>{html.escape(level.heading)}</h2>")
        figures = [_figure(level.svg_href, "Synthesized map")]
        figures.extend(_figure(href, f"Page {number}") for number, href in level.map_pages)
        parts.append(f'<div class="pair">{"".join(figures)}</div>')
        if not level.map_pages:
            parts.append("<p>The survey recorded no map pages for this level.</p>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"
