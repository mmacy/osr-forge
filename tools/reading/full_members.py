"""What the full run reads: each member's maps, pages, caches, and halves.

A *member* is one answer key or one synthetic answer, with the survey, content, and map-reading caches the reference
resolution script resolves it with:

- Each real member groups the map file entries (`maps.json` and the commercial counterpart) that share a key. Its
  caches come from the answer-key specs (`tools/answer_keys/keys.json` and the commercial counterpart), which name
  each map's `stages` directory, and its pages from the pages files.
- Each synthetic member is a directory of `tests/assets/synthetic-maps` with `geometry.json` and `stages/`. Its maps
  are the registered regions of its answer. A region's image is named relative to the answer's directory or its
  `images/` directory. Its pages group the regions by page number and image, labelled `R1`, `R2`, and so on in the
  answer's order. A region with no registration (refused or unregistered in the answer) isn't read, because the
  whole-page request needs the region's grid.

Every map gets a *uid*, unique over the run: a real map's id, or `synthetic/<member>/<region>`.

The halves are the pairs whose offset Claude picks: the real pairs of a halves file (`real` true), which decide the
halves rule, and each synthetic member's half pair, whose true letter is the answer's column offset among the
candidates, or `none` for halves the answer leaves apart.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from osrforge.tracing.truth import GeometryTruth, load_geometry_truth

from .maps import ScreenMap, load_maps
from .screen_wholemap import Page, load_pages

__all__ = ["HalvesSpec", "Member", "RunMap", "load_halves", "real_members", "synthetic_members"]


@dataclass(frozen=True)
class RunMap:
    """One registered region the run reads."""

    uid: str
    member: str
    screen: ScreenMap
    """The region, with the key's style. The run reads with the style the whole-page read gives."""

    page: str
    """The id of the page the region is read on."""

    synthetic: bool


@dataclass(frozen=True)
class Member:
    """One answer key or synthetic answer with its caches."""

    id: str
    key: Path
    stages: Path
    """The directory whose `stages/` holds the caches."""

    maps: tuple[RunMap, ...]
    pages: tuple[Page, ...]
    synthetic: bool
    halves: tuple[HalvesSpec, ...] = field(default=())

    def document(self) -> GeometryTruth:
        """The answer key."""
        return load_geometry_truth(self.key)


@dataclass(frozen=True)
class HalvesSpec:
    """One pair of halves whose column offset Claude picks."""

    id: str
    first: Path
    second: Path
    truth: str | None
    """The true letter, or `None` when it is decided from the candidates (a synthetic member's pair)."""

    real: bool
    first_fit: Path | None = None
    second_fit: Path | None = None
    member: str | None = None
    regions: tuple[str, str] | None = None
    joined_columns: int | None = None
    """The answer's column offset when its halves are joined, for a synthetic member's pair."""


def _resolver(base: Path):  # type: ignore[no-untyped-def]
    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()

    return resolve


def real_members(map_files: list[Path], page_files: list[Path], key_files: list[Path]) -> list[Member]:
    """The real members of the map files, in the order their first map appears.

    Args:
        map_files: The reading measurement's map files.
        page_files: The pages files.
        key_files: The answer-key specs, for each map's `stages` directory.

    Returns:
        One member per key.

    Raises:
        ValueError: If a map has no stages directory, or two maps of one key name different ones.
    """
    stages: dict[str, Path] = {}
    for path in key_files:
        resolve = _resolver(path.parent)
        for item in json.loads(path.read_text())["maps"]:
            if item.get("stages"):
                stages[item["id"]] = resolve(item["stages"]).parent
    pages = {region: page for path in page_files for page in load_pages(path) for _label, region in page.regions}
    grouped: dict[Path, list[ScreenMap]] = {}
    for path in map_files:
        for screen in load_maps(path):
            grouped.setdefault(screen.key, []).append(screen)
    members = []
    for key, screens in grouped.items():
        dirs = {stages.get(screen.id) for screen in screens}
        if None in dirs or len(dirs) != 1:
            raise ValueError(f"the maps of {key} need one stages directory, not {sorted(map(str, dirs))}")
        member_id = key.parent.name
        maps = tuple(
            RunMap(uid=screen.id, member=member_id, screen=screen, page=pages[screen.id].id, synthetic=False)
            for screen in screens
        )
        member_pages = tuple(dict.fromkeys(pages[screen.id] for screen in screens))
        members.append(
            Member(
                id=member_id,
                key=key,
                stages=dirs.pop(),  # type: ignore[arg-type]
                maps=maps,
                pages=member_pages,
                synthetic=False,
            )
        )
    return members


def _image(directory: Path, name: str) -> Path:
    for candidate in (directory / name, directory / "images" / name):
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(f"no image {name} for {directory}")


def synthetic_members(root: Path) -> list[Member]:
    """Every synthetic member under a directory, in name order."""
    members = []
    for directory in sorted(path for path in root.iterdir() if (path / "geometry.json").is_file()):
        key = (directory / "geometry.json").resolve()
        answer = load_geometry_truth(key)
        name = directory.name
        maps: list[RunMap] = []
        by_page: dict[tuple[int, str], list[str]] = {}
        for region in answer.regions:
            if region.registration is None:
                continue
            by_page.setdefault((region.page, region.image), []).append(region.id)
        pages = []
        page_of: dict[str, str] = {}
        for (number, image), regions in sorted(by_page.items()):
            page_id = f"synthetic/{name}/p{number}-{Path(image).stem}"
            pages.append(
                Page(
                    id=page_id,
                    number=number,
                    image=_image(directory, image),
                    regions=tuple((f"R{index}", region) for index, region in enumerate(regions, start=1)),
                    key=key,
                    survey=(directory / "stages" / "survey.json").resolve(),
                )
            )
            page_of.update(dict.fromkeys(regions, page_id))
        for region in answer.regions:
            if region.registration is None:
                continue
            screen = ScreenMap(
                id=region.id,
                style=region.style,
                key=key,
                image=_image(directory, region.image),
                work=None,
                frame=None,
                classifier=None,
            )
            maps.append(
                RunMap(
                    uid=f"synthetic/{name}/{region.id}",
                    member=f"synthetic/{name}",
                    screen=screen,
                    page=page_of[region.id],
                    synthetic=True,
                )
            )
        halves = tuple(
            HalvesSpec(
                id=f"synthetic/{name}/{pair.regions[0]}+{pair.regions[1]}",
                first=_image(directory, answer.region(pair.regions[0]).image),
                second=_image(directory, answer.region(pair.regions[1]).image),
                truth=None,
                real=False,
                member=f"synthetic/{name}",
                regions=pair.regions,
                joined_columns=pair.columns,
            )
            for pair in answer.half_pairs
        )
        members.append(
            Member(
                id=f"synthetic/{name}",
                key=key,
                stages=directory.resolve(),
                maps=tuple(maps),
                pages=tuple(pages),
                synthetic=True,
                halves=halves,
            )
        )
    return members


def load_halves(path: Path) -> list[HalvesSpec]:
    """The pairs of a halves file (the screening's format), real or synthetic as each says."""
    resolve = _resolver(path.parent)
    return [
        HalvesSpec(
            id=item["id"],
            first=resolve(item["first"]),
            second=resolve(item["second"]),
            truth=item["truth"],
            real=bool(item.get("real", False)),
            first_fit=resolve(item["first_fit"]) if item.get("first_fit") else None,
            second_fit=resolve(item["second_fit"]) if item.get("second_fit") else None,
        )
        for item in json.loads(path.read_text())["pairs"]
    ]
