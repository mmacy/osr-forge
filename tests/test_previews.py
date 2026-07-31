"""The SVG renderer and previews index: byte stability, the full edge model, and no timestamp-shaped content."""

import re

from osrlib.crawl.dungeon import AreaSpec, DoorSpec, Edge, EdgeKind, LevelSpec, TransitionSpec, edge_key
from osrlib.crawl.dungeon import Direction as GridDirection

from osrforge.previews import PreviewIndexLevel, render_level_svg, render_previews_index

ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def make_level() -> LevelSpec:
    """A hand-built 4x3 level: a 2x2 room, a corridor east, a doored second room, stairs."""
    edges = {
        edge_key((0, 0), GridDirection.EAST): Edge(kind=EdgeKind.OPEN),
        edge_key((0, 0), GridDirection.SOUTH): Edge(kind=EdgeKind.OPEN),
        edge_key((1, 0), GridDirection.SOUTH): Edge(kind=EdgeKind.OPEN),
        edge_key((0, 1), GridDirection.EAST): Edge(kind=EdgeKind.OPEN),
        edge_key((1, 0), GridDirection.EAST): Edge(kind=EdgeKind.OPEN),
        edge_key((2, 0), GridDirection.EAST): Edge(kind=EdgeKind.DOOR, door=DoorSpec()),
        edge_key((3, 0), GridDirection.SOUTH): Edge(kind=EdgeKind.DOOR, door=DoorSpec(kind="secret")),
    }
    return LevelSpec(
        number=1,
        width=4,
        height=3,
        edges=edges,
        areas=(
            AreaSpec(id="1", name="Hall", cells=((0, 0), (1, 0), (0, 1), (1, 1))),
            AreaSpec(id="2", name="Vault", cells=((3, 0), (3, 1))),
        ),
        transitions=(
            TransitionSpec(
                kind="stairs_down",
                position=(0, 1),
                to_dungeon_id="lair",
                to_level_number=2,
                to_position=(0, 0),
                to_facing=GridDirection.NORTH,
            ),
        ),
        entrance=(0, 0),
    )


def test_byte_stable():
    level = make_level()
    assert render_level_svg("lair", level) == render_level_svg("lair", level)


def test_renders_every_element_kind():
    svg = render_level_svg("lair", make_level())
    assert svg.startswith("<svg xmlns=")
    assert svg.endswith("</svg>\n")
    assert "lair level 1" in svg
    # Area fills, corridor fill (cell (2, 0) is non-area with open edges).
    assert svg.count('fill="#ddd4c0"') == 6
    assert svg.count('fill="#efe9dc"') == 1
    # Walls exist (boundary at minimum), doors: one solid tick, one secret outline.
    assert "<line" in svg
    assert svg.count('fill="#8b5a2b"') == 1
    assert svg.count('stroke="#8b5a2b"') == 1
    # Entrance circle, stairs glyph, both area labels.
    assert "<circle" in svg
    assert "▼" in svg
    assert ">1</text>" in svg and ">2</text>" in svg


def test_dead_cells_get_no_fill():
    # Cells that are neither area nor open-edged corridor draw nothing.
    svg = render_level_svg("lair", make_level())
    fills = svg.count('fill="#ddd4c0"') + svg.count('fill="#efe9dc"')
    assert fills == 7  # of 12 grid cells, only 6 area + 1 corridor are active


def test_no_timestamp_shaped_content():
    assert not ISO_TIMESTAMP.search(render_level_svg("lair", make_level()))


def test_ruler_labels_every_row_and_column():
    # The ruler prints the exact 0-based coordinates a geometry override's
    # `cells` and edge keys use: one column label per x, one row label per y.
    svg = render_level_svg("lair", make_level())
    ruler_labels = re.findall(r'fill="#888888">(\d+)</text>', svg)
    assert ruler_labels == ["0", "1", "2", "3", "0", "1", "2"]


def make_index_levels() -> tuple[PreviewIndexLevel, ...]:
    return (
        PreviewIndexLevel(
            heading="The Bone Barrow — level 1",
            svg_href="bone-barrow.1.svg",
            map_pages=((4, "../pages/0004.png"), (12, "../pages/0012.png")),
        ),
        PreviewIndexLevel(heading="The Bone Barrow — level 2", svg_href="bone-barrow.2.svg", map_pages=()),
    )


def test_index_is_byte_stable_and_pairs_each_level():
    first = render_previews_index("The Example Barrow", make_index_levels())
    assert first == render_previews_index("The Example Barrow", make_index_levels())
    assert first.startswith("<!DOCTYPE html>")
    assert first.endswith("</html>\n")
    assert "The Example Barrow — previews" in first
    assert '<img src="bone-barrow.1.svg" alt="Synthesized map">' in first
    assert '<img src="../pages/0004.png" alt="Page 4">' in first
    assert '<img src="../pages/0012.png" alt="Page 12">' in first
    # The mapless level still gets its SVG, plus an honest note.
    assert '<img src="bone-barrow.2.svg" alt="Synthesized map">' in first
    assert "The survey recorded no map pages for this level." in first
    assert not ISO_TIMESTAMP.search(first)


def test_index_escapes_module_text():
    level = PreviewIndexLevel(heading="Caves of the <Unknown> & Co", svg_href="caves.1.svg", map_pages=())
    page = render_previews_index("Tomb <of> the & Serpent", (level,))
    assert "Tomb &lt;of&gt; the &amp; Serpent — previews" in page
    assert "Caves of the &lt;Unknown&gt; &amp; Co" in page
    assert "<Unknown>" not in page
