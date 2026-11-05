"""The bar for walls read as open: the classifier's rate on the answer keys, per style.

For each map, the classifier's reading (from `tools.answer_keys.run`) is moved onto the key's registered grid and
scored with [`score_trace`][osrforge.tracing.score.score_trace] in place of the key's panel. A style's rate is its
maps' walls read as open over their edges in the scorer's universe (the key's edges that touch floor or water),
pooled. The same rate over the key's blocked edges, and both rates without the partly floor cells, are reported
beside it. A reading meets the bar when its rate over the same edges is no higher.

Run from the osr-forge checkout:

    uv run python -m tools.reading.bar <classifier readings dir> <map file>... [--out <json>]
"""

import argparse
import json
from pathlib import Path

from tools.answer_keys.frame import crop_panel
from tools.answer_keys.reading import load_reading

from osrforge.tracing.fivefoot import convert_five_foot
from osrforge.tracing.score import EdgeTally
from osrforge.tracing.truth import PASSABLE_LETTERS, Panel

from .maps import STYLES, ScreenMap, key_document, key_panel, load_maps
from .traces import score_panel, tally, universe

__all__ = ["classifier_panel", "key_blocked", "main", "style_rates"]


def classifier_panel(readings: Path, screen: ScreenMap) -> Panel:
    """The classifier's reading of a map, on the key's registered grid."""
    panel = load_reading(readings / f"{screen.id}.json").panel
    key = key_panel(screen)
    x0, y0 = screen.frame or (0, 0)
    if (x0, y0) != (0, 0) or (panel.width, panel.height) != (key.width, key.height):
        panel = crop_panel(panel, (x0, y0, key.width, key.height))
    return panel


def key_blocked(screen: ScreenMap, whole: bool) -> int:
    """The key's blocked edges in the scorer's universe, on the converted panel."""
    panel = key_panel(screen)
    if panel.scale_feet == 5:
        panel = convert_five_foot(panel).panel
    return sum(panel.edges.at(*edge) not in PASSABLE_LETTERS for edge in universe(panel, whole))


def _rates(edges: EdgeTally, blocked: int) -> dict[str, float | int | None]:
    return {
        "edges": edges.total,
        "right": edges.right,
        "walls_read_as_open": edges.walls_read_as_open,
        "open_read_as_blocked": edges.open_read_as_blocked,
        "key_blocked": blocked,
        "rate_over_edges": edges.walls_read_as_open / edges.total if edges.total else None,
        "rate_over_blocked": edges.walls_read_as_open / blocked if blocked else None,
    }


def style_rates(readings: Path, maps: list[ScreenMap]) -> dict[str, object]:
    """Score the classifier on every map and pool the figures by style.

    Args:
        readings: The directory of classifier reading files.
        maps: The maps.

    Returns:
        `{"maps": {id: {"style", "every": figures, "whole": figures}}, "styles": {style: {"maps", "every",
        "whole"}}}`, where each figures dict has the edge tally and both rates.
    """
    per_map: dict[str, dict[str, object]] = {}
    tallies: dict[str, dict[str, list[EdgeTally]]] = {}
    blocked: dict[str, dict[str, int]] = {}
    for screen in maps:
        every, whole = score_panel(key_document(screen.key), classifier_panel(readings, screen))
        figures = {}
        for name, scores in (("every", every), ("whole", whole)):
            count = key_blocked(screen, name == "whole")
            figures[name] = _rates(scores.edges.all, count)
            tallies.setdefault(screen.style, {}).setdefault(name, []).append(scores.edges.all)
            blocked.setdefault(screen.style, {}).setdefault(name, 0)
            blocked[screen.style][name] += count
        per_map[screen.id] = {"style": screen.style, **figures}
    styles: dict[str, object] = {}
    for style in STYLES:
        if style not in tallies:
            continue
        styles[style] = {
            "maps": [screen.id for screen in maps if screen.style == style],
            **{name: _rates(tally(tallies[style][name]), blocked[style][name]) for name in ("every", "whole")},
        }
    return {"maps": per_map, "styles": styles}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The bar for walls read as open, from the classifier.")
    parser.add_argument("readings", type=Path)
    parser.add_argument("map_files", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    maps = [screen for path in args.map_files for screen in load_maps(path)]
    result = style_rates(args.readings, maps)
    for style, figures in result["styles"].items():  # type: ignore[union-attr]
        for name in ("every", "whole"):
            f = figures[name]
            print(
                f"{style:12} {name:5} edges {f['edges']:5} walls read as open {f['walls_read_as_open']:4} "
                f"rate {f['rate_over_edges']:.4f} over edges, {f['rate_over_blocked']:.4f} over {f['key_blocked']} "
                f"blocked; right {f['right']}"
            )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
