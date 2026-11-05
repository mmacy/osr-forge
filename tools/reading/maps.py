"""The maps the reading measurement runs on, each tied to its answer key.

A map file is JSON: `{"maps": [...]}`, each entry with

- `id`: the region and panel id in the key
- `style`: the region's truth style
- `key`: the answer key's `geometry.json`
- `image`: the map image the region is cut from
- `work`: the answer key's work directory for this map, whose `clef/answers.jsonl` holds the Clef answers the key
  was built with, or null
- `frame`: where the key's registered grid starts on the answer key's working grid, `[x, y]` in working cells, or
  null when the two grids are one
- `classifier`: the classifier spec file and id that read this map, `[spec file, id]`

Relative paths resolve against the map file's directory, and `~` expands. `maps.json` beside this module lists the
committed Chaotic Caves maps. The file for commercial maps stays outside the repository.
"""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image

from osrforge.tracing.truth import GeometryTruth, Panel, Region, load_geometry_truth

__all__ = ["STYLES", "ScreenMap", "key_document", "key_panel", "key_region", "load_maps", "load_rgb"]

STYLES: tuple[str, ...] = ("tsr_blue", "black_white", "dark_rock", "cave")
"""The plan-view styles the stage traces, in the order the reports list them."""


@dataclass(frozen=True)
class ScreenMap:
    """One map region with an answer key."""

    id: str
    style: str
    key: Path
    image: Path
    work: Path | None
    frame: tuple[int, int] | None
    classifier: tuple[Path, str] | None


def load_maps(path: Path) -> list[ScreenMap]:
    """Read a map file.

    Args:
        path: The JSON file.

    Returns:
        The entries in file order, with paths made absolute.
    """
    document = json.loads(path.read_text())
    base = path.parent

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    maps = []
    for item in document["maps"]:
        classifier = item.get("classifier")
        maps.append(
            ScreenMap(
                id=item["id"],
                style=item["style"],
                key=resolve(item["key"]),
                image=resolve(item["image"]),
                work=resolve(item["work"]) if item.get("work") else None,
                frame=tuple(item["frame"]) if item.get("frame") else None,  # type: ignore[arg-type]
                classifier=(resolve(classifier[0]), classifier[1]) if classifier else None,
            )
        )
    return maps


@cache
def key_document(path: Path) -> GeometryTruth:
    """The answer key at a path, loaded once."""
    return load_geometry_truth(path)


def key_panel(screen: ScreenMap) -> Panel:
    """The key's panel for a map."""
    return key_document(screen.key).panel(screen.id)


def key_region(screen: ScreenMap) -> Region:
    """The key's region for a map."""
    return key_document(screen.key).region(screen.id)


@cache
def load_rgb(path: Path) -> np.ndarray:
    """A map image as an RGB array, loaded once."""
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))
