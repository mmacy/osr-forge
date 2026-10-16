"""A reading on disk: the truth format's `Region` and `Panel`, with the classifier's confidences beside them.

A reading file is JSON with these keys:

- `map`: the map spec's id
- `image`: the image file the reading was made from
- `style`: the classifier style
- `fit`: the grid fit's figures
- `region`: a truth-format `Region`, with the registration
- `panel`: a truth-format `Panel`, with the cells, edges, partial cells, and cave cells
- `confidence`: `cells`, `vertical`, and `horizontal` grids of numbers, one per letter of the panel's grids
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from osrforge.tracing.truth import Compass, EdgeGrids, Legend, Panel, PanelRegion, Region, Scale, cell_key

from .classify import Reading
from .gridfit import GridFit
from .spec import MapSpec


@dataclass(frozen=True)
class ReadingFile:
    """A reading loaded from disk."""

    map: str
    image: str
    style: str
    fit: dict[str, object]
    region: Region
    panel: Panel
    cell_confidence: np.ndarray
    vertical_confidence: np.ndarray
    horizontal_confidence: np.ndarray


def to_region(spec: MapSpec, fit: GridFit) -> Region:
    """The region a spec and a fit describe."""
    return Region(
        id=spec.id,
        page=spec.page,
        image=Path(spec.image).name,
        style=spec.truth_style,  # type: ignore[arg-type]
        columns=fit.columns,
        rows=fit.rows,
        scale=Scale(feet=spec.scale_feet, printed=spec.scale_printed),
        compass=Compass(north=spec.north, printed=spec.compass_printed),  # type: ignore[arg-type]
        legend=Legend(printed=spec.legend_printed),
        verdict="registered",
        registration=fit.registration(),
    )


def to_panel(spec: MapSpec, reading: Reading) -> Panel:
    """The panel a reading fills."""
    rows, columns = reading.cells.shape
    cells = tuple("".join(reading.cells[y]) for y in range(rows))
    vertical = tuple("".join(reading.vertical[y]) for y in range(rows))
    horizontal = tuple("".join(reading.horizontal[j]) for j in range(rows + 1))
    partial = tuple(cell_key((x, y)) for y in range(rows) for x in range(columns) if reading.partial[y, x])
    cave = tuple(cell_key((x, y)) for y in range(rows) for x in range(columns) if reading.cave[y, x])
    return Panel(
        id=spec.id,
        regions=(PanelRegion(region=spec.id, origin=(0, 0)),),
        scale_feet=spec.scale_feet,  # type: ignore[arg-type]
        cells=cells,
        edges=EdgeGrids(vertical=vertical, horizontal=horizontal),
        partial=partial,
        cave=cave,
    )


def save_reading(path: Path, spec: MapSpec, fit: GridFit, reading: Reading) -> None:
    """Write a reading file.

    Args:
        path: Where to write it.
        spec: The map spec the reading was made from.
        fit: The grid fit.
        reading: The classifier's reading.
    """
    region = to_region(spec, fit)
    panel = to_panel(spec, reading)
    document = {
        "map": spec.id,
        "image": spec.image,
        "style": spec.style,
        "fit": fit.summary(),
        "region": region.model_dump(mode="json"),
        "panel": panel.model_dump(mode="json"),
        "confidence": {
            "cells": np.round(reading.cell_confidence, 3).tolist(),
            "vertical": np.round(reading.vertical_confidence, 3).tolist(),
            "horizontal": np.round(reading.horizontal_confidence, 3).tolist(),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1) + "\n")


def load_reading(path: Path) -> ReadingFile:
    """Read a reading file back, validating its region and panel through the truth models."""
    document = json.loads(path.read_text())
    return ReadingFile(
        map=document["map"],
        image=document["image"],
        style=document["style"],
        fit=document["fit"],
        region=Region.model_validate(document["region"]),
        panel=Panel.model_validate(document["panel"]),
        cell_confidence=np.array(document["confidence"]["cells"]),
        vertical_confidence=np.array(document["confidence"]["vertical"]),
        horizontal_confidence=np.array(document["confidence"]["horizontal"]),
    )
