"""The answer-key spec: which maps a run builds keys for, and what each needs beyond its classifier spec.

A spec file is JSON with `work`, the directory every map's requests, answers, crops, and settled calls go
under, and `maps`, a list of entries:

- `id`: the classifier spec's id, which names the map's work directory and its reading files
- `specs`: the classifier spec file, relative to the spec file's directory
- `member`: the corpus member, such as `jn1-chaotic-caves`, or `synthetic/<name>` for a synthetic map
- `style_note`: the map's style and legend in words, which every Claude request about the map carries
- `legend_from_page`: the page whose legend the region uses when it prints none, or null
- `levels`: the levels drawn on the region, each `[dungeon id, number]`, in survey order
- `survey`: the survey cache that names the levels and their keys, relative to the spec file's directory
- `stages`: the directory of the survey, content, and map-reading caches the resolver reads
- `answer`: for a synthetic map, its exact answer, to score the settled reading against
- `key`: where the finished key is written

`keys.json` beside this module covers the committed Chaotic Caves maps and the two synthetic maps the pipeline
is checked on. The Palace spec stays outside the repository, under `~/.re-pod/map-plan/osr-suite/answer-keys/`.
"""

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import numpy as np
from PIL import Image

from .gridfit import GridFit
from .run import read_map
from .spec import MapSpec, load_specs

__all__ = ["KeySpec", "MapContext", "context", "load_key_specs"]


@dataclass(frozen=True)
class KeySpec:
    """One map's entry."""

    id: str
    specs: Path
    member: str
    style_note: str
    work: Path
    key: Path
    levels: tuple[tuple[str, int], ...] = ()
    survey: Path | None = None
    stages: Path | None = None
    answer: Path | None = None
    legend_from_page: int | None = None
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def directory(self) -> Path:
        """The map's work directory."""
        return self.work / self.id


def load_key_specs(path: Path) -> list[KeySpec]:
    """Read a spec file.

    Args:
        path: The JSON file.

    Returns:
        The entries, with paths made absolute.
    """
    document = json.loads(path.read_text())
    work = Path(document["work"]).expanduser()
    base = path.parent

    def resolve(value: str | None) -> Path | None:
        if value is None:
            return None
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    known = {"id", "specs", "member", "style_note", "key", "levels", "survey", "stages", "answer", "legend_from_page"}
    specs = []
    for item in document["maps"]:
        specs.append(
            KeySpec(
                id=item["id"],
                specs=resolve(item["specs"]) or base,
                member=item["member"],
                style_note=item["style_note"],
                work=work,
                key=resolve(item["key"]) or work / item["id"] / "geometry.json",
                levels=tuple((level[0], int(level[1])) for level in item.get("levels", [])),
                survey=resolve(item.get("survey")),
                stages=resolve(item.get("stages")),
                answer=resolve(item.get("answer")),
                legend_from_page=item.get("legend_from_page"),
                extra={key: value for key, value in item.items() if key not in known},
            )
        )
    return specs


@dataclass(frozen=True)
class MapContext:
    """A map's spec, image, and grid fit, loaded once."""

    key: KeySpec
    spec: MapSpec
    rgb: np.ndarray
    fit: GridFit


@cache
def _load(specs_path: Path, map_id: str) -> tuple[MapSpec, np.ndarray, GridFit]:
    specs = {spec.id: spec for spec in load_specs(specs_path)}
    spec = specs[map_id]
    rgb = np.asarray(Image.open(spec.image).convert("RGB"))
    fit, _reading = read_map(spec, rgb)
    return spec, rgb, fit


def context(key: KeySpec) -> MapContext:
    """Load a map's spec, image, and fit.

    Args:
        key: The map's entry.

    Returns:
        The context. The fit is the classifier's, so every crop and every reading share one registration.
    """
    spec, rgb, fit = _load(key.specs, key.id)
    return MapContext(key=key, spec=spec, rgb=rgb, fit=fit)
