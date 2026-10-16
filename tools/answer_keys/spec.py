"""Map specs: which image to read, in which style, with which levels and region.

A spec file is JSON: a list of objects, each with the fields of `MapSpec`.
`maps.json` beside this module lists the committed Chaotic Caves maps. Specs
for commercial maps stay outside the repository.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .ink import Levels

TRUTH_STYLES = {
    "tsr_blue": "tsr_blue",
    "tsr_blue_white": "tsr_blue",
    "black_white": "black_white",
    "cave": "cave",
    "dark_rock": "dark_rock",
}
"""The truth format's style for each classifier style."""


@dataclass(frozen=True)
class MapSpec:
    """One region of one map image."""

    id: str
    image: str
    """The image path. A relative path is taken from the spec file's directory."""

    style: str
    """A classifier style name from `styles.STYLES`."""

    levels: Levels
    rect: tuple[int, int, int, int]
    """The region `(x0, y0, x1, y1)` in pixels, the end exclusive."""

    pitch_range: tuple[float, float]
    """The least and greatest grid pitch to consider, in pixels."""

    page: int = 1
    scale_feet: int = 10
    scale_printed: bool = True
    north: str = "top"
    compass_printed: bool = False
    legend_printed: bool = False
    line_ink_max: float = 170.0
    """The gray level below which a pixel counts as ink for finding grid lines."""

    fill_kind: str = "rock"
    """What the map's fill stands for: `rock`, or `water` on a map whose legend says so."""

    notes: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def truth_style(self) -> str:
        """The truth format's style name."""
        return TRUTH_STYLES[self.style]


def load_specs(path: Path) -> list[MapSpec]:
    """Read a spec file.

    Args:
        path: The JSON file.

    Returns:
        The specs, with image paths made absolute.
    """
    specs = []
    for item in json.loads(path.read_text()):
        levels = item.pop("levels")
        fill = levels.get("fill")
        image = Path(item.pop("image"))
        if not image.is_absolute():
            image = (path.parent / image).resolve()
        known = {name for name in MapSpec.__dataclass_fields__}
        extra = {key: item[key] for key in item if key not in known}
        fields = {key: item[key] for key in item if key in known}
        fields["rect"] = tuple(fields["rect"])
        fields["pitch_range"] = tuple(fields["pitch_range"])
        specs.append(
            MapSpec(
                image=str(image),
                levels=Levels(
                    channel=levels.get("channel", "gray"),
                    ink_max=levels["ink_max"],
                    dark_max=levels.get("dark_max", levels["ink_max"]),
                    wall_max=levels.get("wall_max"),
                    paper_min=levels["paper_min"],
                    fill=tuple(fill) if fill else None,
                ),
                extra=extra,
                **fields,
            )
        )
    return specs
