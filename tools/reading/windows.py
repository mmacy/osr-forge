"""The screening windows: a few 12 by 12 windows of each map, where Clef's option sets are compared.

A windows file is JSON: `{"windows": [[map id, x, y, width, height], ...]}` in the key's grid cells. Each map's
windows are the windows of its 12-cell tiling with the most edges in the scorer's universe, chosen so that every
rare letter the map has (printed open line, window, locked door, secret door) is in one of them, and the whole map
where it is small. `windows.json` beside this module lists the committed maps' windows; the file for commercial maps
stays outside the repository.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from osrforge.tracing.truth import Panel

from .traces import universe

__all__ = ["Window", "load_windows", "window_cells", "window_edges"]


@dataclass(frozen=True)
class Window:
    """One window of one map."""

    map: str
    x: int
    y: int
    width: int
    height: int

    def holds(self, cell: tuple[int, int]) -> bool:
        """Whether a cell lies in the window."""
        return self.x <= cell[0] < self.x + self.width and self.y <= cell[1] < self.y + self.height


def load_windows(path: Path) -> list[Window]:
    """Read a windows file."""
    return [Window(*item) for item in json.loads(path.read_text())["windows"]]


def window_edges(panel: Panel, windows: Sequence[Window]) -> list[tuple[tuple[int, int], str]]:
    """The key's universe edges named by a cell inside one of the windows."""
    return [edge for edge in universe(panel) if any(window.holds(edge[0]) for window in windows)]


def window_cells(panel: Panel, windows: Sequence[Window]) -> list[tuple[int, int]]:
    """Every cell inside one of the windows, in page order."""
    return [
        (x, y)
        for y in range(panel.height)
        for x in range(panel.width)
        if any(window.holds((x, y)) for window in windows)
    ]
