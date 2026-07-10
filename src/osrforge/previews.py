"""The deterministic SVG level renderer.

Pure string assembly with no dependencies and no timestamps — previews are one
of the three pure artifacts, so byte stability across runs is a contract. The
renderer supports the full osrlib `Edge` model from birth: v1 synthesis emits
only `open` edges, but phase 3's override re-renders will carry doors.

Element emission order is pinned (cells, walls, doors, entrance, transitions,
labels — each group in a deterministic sort) so identical specs render
identical bytes.
"""

from osrlib.crawl.dungeon import Direction, EdgeKind, LevelSpec, Position

__all__ = ["CELL_PX", "render_level_svg"]

CELL_PX = 24
"""The fixed cell size in pixels."""

_PAD = 12
_TITLE_HEIGHT = 20

_AREA_FILL = "#ddd4c0"
_CORRIDOR_FILL = "#efe9dc"
_WALL_STROKE = "#333333"
_DOOR_FILL = "#8b5a2b"
_ENTRANCE_STROKE = "#1a7f37"
_TEXT_FILL = "#333333"

_TRANSITION_GLYPHS = {"stairs_up": "▲", "stairs_down": "▼", "trapdoor": "T", "chute": "C"}


def _row_major(cells: set[Position]) -> list[Position]:
    return sorted(cells, key=lambda cell: (cell[1], cell[0]))


def _cell_origin(cell: Position) -> tuple[int, int]:
    return (_PAD + cell[0] * CELL_PX, _PAD + _TITLE_HEIGHT + cell[1] * CELL_PX)


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
    ▼ down).

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
    width_px = max(level.width * CELL_PX, len(title) * 8) + 2 * _PAD
    height_px = level.height * CELL_PX + 2 * _PAD + _TITLE_HEIGHT
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_px}" height="{height_px}" '
        f'viewBox="0 0 {width_px} {height_px}">'
    )
    parts.append(
        f'<text x="{_PAD}" y="{_PAD + 4}" font-family="monospace" font-size="12" fill="{_TEXT_FILL}">{title}</text>'
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
