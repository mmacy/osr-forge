"""Run the classifier over the maps of a spec file and write a reading for each.

Run from the osr-forge checkout:

    uv run python -m tools.answer_keys.run tools/answer_keys/maps.json --out <dir>

Each map gets `<dir>/<id>.json`. `--only <id>` runs one map, and `--overlay x0,y0` also writes a 12 by 12 overlay
of the reading at that window to `<dir>/<id>_<x0>_<y0>.png`.
"""

import argparse
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .classify import classify
from .crops import render_reading
from .gridfit import fit_grid
from .ink import channel_image, materials
from .reading import save_reading, to_panel
from .spec import MapSpec, load_specs
from .styles import STYLES


def read_map(spec: MapSpec, rgb: np.ndarray):
    """Fit the grid and classify one map. Returns `(fit, reading)`."""
    style = STYLES[spec.style]
    gray = channel_image(rgb, spec.levels.channel)
    fit = fit_grid(gray < spec.line_ink_max, spec.rect, spec.pitch_range)
    close_radius = max(3, round(0.1 * fit.pitch))
    mats = materials(rgb, spec.levels, close_radius)
    reading = classify(mats, fit, style, "~" if spec.fill_kind == "water" else "#")
    return fit, reading


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("specs", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default=None)
    parser.add_argument("--overlay", default=None)
    args = parser.parse_args()
    for spec in load_specs(args.specs):
        if args.only and spec.id != args.only:
            continue
        started = time.time()
        rgb = np.asarray(Image.open(spec.image).convert("RGB"))
        fit, reading = read_map(spec, rgb)
        save_reading(args.out / f"{spec.id}.json", spec, fit, reading)
        summary = fit.summary()
        print(
            f"{spec.id}: {fit.columns} by {fit.rows} cells, pitch {summary['x']['pitch']} by {summary['y']['pitch']}, "
            f"rms {summary['rms_cells']} cells, max {summary['max_cells']} cells, {time.time() - started:.1f}s"
        )
        if args.overlay:
            x0, y0 = (int(v) for v in args.overlay.split(","))
            panel = to_panel(spec, reading)
            partial = {tuple(int(v) for v in c.split(",")) for c in panel.partial}
            out = args.out / f"{spec.id}_{x0}_{y0}.png"
            render_reading(
                rgb, fit, panel.cells, panel.edges.vertical, panel.edges.horizontal, partial, x0, y0, 12, 12, out
            )  # type: ignore[arg-type]
            print("  overlay", out)


if __name__ == "__main__":
    main()
