"""The map images: extraction at native resolution, and spreads, duplicates, hidden images, and blanked pages.

The PDFs are built by `pdf_support` from page manifests in the truth format (`Page` and `PageImage`), which is
how the synthetic maps record their spreads, duplicates, hidden images, and blanked pages. The last test runs
over every committed synthetic map and so needs the generator chunks as well as this one.
"""

import hashlib
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from osrforge.errors import PdfError
from osrforge.tracing.images import MapImage, extract_map_images, map_image_filename
from osrforge.tracing.truth import GeometryTruth, Page, PageImage
from osrforge.workdir import Workdir
from pdf_support import PAGE_HEIGHT, PAGE_WIDTH, Placement, build_pdf, build_pdf_from_manifest

CHUNK = "chunk: map-images"
SYNTHETIC = Path(__file__).parent / "assets" / "synthetic-maps"


def picture(seed: int, size: tuple[int, int] = (120, 60)) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))


def pixels(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def same_picture(path: Path, image: Image.Image) -> bool:
    return np.array_equal(pixels(path), np.asarray(image.convert("RGB")))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def full_page(top: float = PAGE_HEIGHT, bottom: float = 0.0) -> tuple[float, float, float, float]:
    return (0.0, bottom, PAGE_WIDTH, top)


def check_files(images: tuple[MapImage, ...], out_dir: Path) -> None:
    for image in images:
        path = out_dir / image.file
        assert path.is_file(), image.file
        assert image.file == map_image_filename(image.pages[0], image.index)
        assert sha256(path) == image.sha256
        with Image.open(path) as png:
            assert png.format == "PNG" and png.size == (image.width, image.height)
    assert {path.name for path in out_dir.iterdir()} >= {image.file for image in images}


def test_workdir_has_a_maps_directory(tmp_path: Path):
    workdir = Workdir(tmp_path)
    assert workdir.maps_dir == tmp_path / "maps"
    assert workdir.map_png(38, 0) == tmp_path / "maps" / "p0038-0.png"
    assert map_image_filename(38, 2) == "p0038-2.png"


def test_map_image_model():
    image = MapImage(file="p0001-0.png", pages=(1,), index=0, width=10, height=10, sha256="ab")
    assert not image.spread
    with pytest.raises(ValueError):
        MapImage(file="x", pages=(), index=0, width=1, height=1, sha256="ab")
    with pytest.raises(ValueError):
        MapImage(file="x", pages=(1, 2, 3), index=0, width=1, height=1, sha256="ab")


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_each_listed_page_yields_its_image_at_native_resolution(tmp_path: Path):
    first, second = picture(1), picture(2, (90, 140))
    pdf = build_pdf(tmp_path / "m.pdf", [Placement(1, first, full_page()), Placement(2, second, (20, 20, 30, 30))])
    out = tmp_path / "maps"
    images = extract_map_images(pdf, pages=[1, 2], blanked=[], out_dir=out)
    assert [(i.file, i.pages, i.index, i.width, i.height, i.spread) for i in images] == [
        ("p0001-0.png", (1,), 0, 120, 60, False),
        ("p0002-0.png", (2,), 0, 90, 140, False),
    ]
    check_files(images, out)
    # The second image is drawn 10 points wide on the page and still comes out as its stored 90 by 140 pixels.
    assert same_picture(out / "p0001-0.png", first)
    assert same_picture(out / "p0002-0.png", second)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_jpeg_stored_image_comes_out_as_its_stored_pixels(tmp_path: Path):
    source = picture(3, (64, 48))
    pdf = build_pdf(tmp_path / "m.pdf", [Placement(1, source, full_page(), jpeg=True)])
    out = tmp_path / "maps"
    (image,) = extract_map_images(pdf, pages=[1], blanked=[], out_dir=out)
    assert (image.width, image.height) == (64, 48)
    buffer = io.BytesIO()
    source.save(buffer, "JPEG", quality=95)
    with Image.open(io.BytesIO(buffer.getvalue())) as decoded:
        difference = np.abs(
            pixels(out / image.file).astype(np.int16) - np.asarray(decoded.convert("RGB")).astype(np.int16)
        )
    assert difference.max() <= 2


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_only_listed_pages_are_read_and_blanked_pages_are_skipped(tmp_path: Path):
    pdf = build_pdf(
        tmp_path / "m.pdf",
        [
            Placement(1, picture(1), full_page()),
            Placement(2, picture(2), full_page()),
            Placement(3, picture(3), full_page()),
        ],
    )
    out = tmp_path / "maps"
    images = extract_map_images(pdf, pages=[1, 3], blanked=[], out_dir=out)
    assert [i.pages for i in images] == [(1,), (3,)]
    images = extract_map_images(pdf, pages=[1, 2, 3], blanked=[2], out_dir=out)
    assert [i.pages for i in images] == [(1,), (3,)]
    assert not (out / "p0002-0.png").exists()
    with pytest.raises(PdfError):
        extract_map_images(pdf, pages=[4], blanked=[], out_dir=out)
    with pytest.raises(PdfError):
        extract_map_images(tmp_path / "absent.pdf", pages=[1], blanked=[], out_dir=out)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_same_image_on_two_pages_counts_once(tmp_path: Path):
    twice = picture(4)
    pdf = build_pdf(
        tmp_path / "m.pdf",
        [Placement(1, twice, full_page()), Placement(2, picture(5), full_page()), Placement(3, twice, full_page())],
    )
    out = tmp_path / "maps"
    images = extract_map_images(pdf, pages=[1, 2, 3], blanked=[], out_dir=out)
    assert [(i.file, i.pages, i.spread) for i in images] == [
        ("p0001-0.png", (1, 3), False),
        ("p0002-0.png", (2,), False),
    ]
    check_files(images, out)
    assert same_picture(out / "p0001-0.png", twice)

    # A duplicate on a blanked page doesn't count: the kept image lists the pages it was read from.
    images = extract_map_images(pdf, pages=[1, 2, 3], blanked=[3], out_dir=tmp_path / "maps2")
    assert [i.pages for i in images] == [(1,), (2,)]


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_one_image_across_a_spread_is_one_map(tmp_path: Path):
    wide = picture(6, (240, 60))
    pdf = build_pdf(
        tmp_path / "m.pdf",
        [
            Placement(1, wide, (0.0, 100.0, 2 * PAGE_WIDTH, 200.0)),
            Placement(2, wide, (-PAGE_WIDTH, 100.0, PAGE_WIDTH, 200.0)),
        ],
    )
    out = tmp_path / "maps"
    (image,) = extract_map_images(pdf, pages=[1, 2], blanked=[], out_dir=out)
    assert (image.file, image.pages, image.spread, image.width, image.height) == ("p0001-0.png", (1, 2), True, 240, 60)
    assert same_picture(out / image.file, wide)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_an_image_drawn_over_most_of_an_earlier_one_hides_it(tmp_path: Path):
    """The Lost City's page 20 draws a grey scan over a hidden 1-bit scan: the page shows the later one."""
    grey, shown = picture(7), picture(8)
    pdf = build_pdf(
        tmp_path / "m.pdf",
        [Placement(1, grey, full_page()), Placement(1, shown, (0.0, 0.0, PAGE_WIDTH, 0.8 * PAGE_HEIGHT))],
    )
    out = tmp_path / "maps"
    (image,) = extract_map_images(pdf, pages=[1], blanked=[], out_dir=out)
    assert (image.file, image.pages, image.index) == ("p0001-1.png", (1,), 1)
    assert same_picture(out / image.file, shown)
    assert not (out / "p0001-0.png").exists()


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_an_overlap_of_less_than_half_hides_nothing(tmp_path: Path):
    """A legend or an inset drawn over the corner of a map leaves the map in place."""
    big, inset = picture(9), picture(10, (40, 40))
    pdf = build_pdf(
        tmp_path / "m.pdf",
        [Placement(1, big, full_page()), Placement(1, inset, (0.0, 0.0, PAGE_WIDTH, 0.3 * PAGE_HEIGHT))],
    )
    out = tmp_path / "maps"
    images = extract_map_images(pdf, pages=[1], blanked=[], out_dir=out)
    assert [(i.file, i.index) for i in images] == [("p0001-0.png", 0), ("p0001-1.png", 1)]
    assert same_picture(out / "p0001-0.png", big) and same_picture(out / "p0001-1.png", inset)


def expected_from_manifest(pages: tuple[Page, ...]) -> dict[str, tuple[tuple[int, ...], bool, int]]:
    """Each kept manifest file to its (pages, spread, index), by the extraction rules."""
    seen: dict[str, tuple[list[int], bool, int]] = {}
    for page in pages:
        if page.blanked:
            continue
        for index, image in enumerate(page.images):
            if image.hidden:
                continue
            if image.file not in seen:
                seen[image.file] = ([], page.spread_with is not None, index)
            seen[image.file][0].append(page.number)
    return {file: (tuple(numbers), spread, index) for file, (numbers, spread, index) in seen.items()}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_manifest_with_every_case_extracts_as_the_rules_say(tmp_path: Path):
    pictures = {"map.png": picture(11), "twice.png": picture(12), "under.png": picture(13), "over.png": picture(14)}
    pictures["wide.png"] = picture(15, (240, 60))
    pages = (
        Page(number=1, images=(PageImage(file="under.png", hidden=True), PageImage(file="over.png"))),
        Page(number=2, images=(PageImage(file="wide.png"),), spread_with=3),
        Page(number=3, images=(PageImage(file="wide.png"),), spread_with=2),
        Page(number=4, images=(PageImage(file="twice.png"),)),
        Page(number=5, images=(PageImage(file="map.png"),), blanked=True),
        Page(number=6, images=(PageImage(file="twice.png"), PageImage(file="map.png"))),
    )
    pdf = build_pdf_from_manifest(tmp_path / "m.pdf", pages, pictures)
    out = tmp_path / "maps"
    images = extract_map_images(pdf, pages=[1, 2, 3, 4, 5, 6], blanked=[5], out_dir=out)
    check_files(images, out)
    by_pixels = {
        image.file: next(name for name, pic in pictures.items() if same_picture(out / image.file, pic))
        for image in images
    }
    got = {by_pixels[image.file]: (image.pages, image.spread, image.index) for image in images}
    assert got == expected_from_manifest(pages)
    assert got == {
        "over.png": ((1,), False, 1),
        "wide.png": ((2, 3), True, 0),
        "twice.png": ((4, 6), False, 0),
        "map.png": ((6,), False, 1),
    }


def map_dirs() -> list[Path]:
    if not SYNTHETIC.is_dir():
        return []
    return sorted(path for path in SYNTHETIC.iterdir() if path.is_dir())


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_every_committed_synthetic_map_extracts_as_its_manifest_says(tmp_path: Path):
    """Needs the committed synthetic maps, so it stays marked until the generator chunks land as well."""
    dirs = map_dirs()
    assert dirs, "no synthetic maps are committed"
    for map_dir in dirs:
        truth = GeometryTruth.model_validate_json((map_dir / "geometry.json").read_text(encoding="utf-8"))
        assert truth.pages, map_dir.name
        pictures = {
            image.file: Image.open(map_dir / "images" / image.file).convert("RGB")
            for page in truth.pages
            for image in page.images
        }
        pdf = build_pdf_from_manifest(tmp_path / f"{map_dir.name}.pdf", truth.pages, pictures)
        out = tmp_path / map_dir.name
        listed = [page.number for page in truth.pages]
        blanked = [page.number for page in truth.pages if page.blanked]
        images = extract_map_images(pdf, pages=listed, blanked=blanked, out_dir=out)
        check_files(images, out)
        by_pixels = {
            image.file: next(name for name, pic in pictures.items() if same_picture(out / image.file, pic))
            for image in images
        }
        got = {by_pixels[image.file]: (image.pages, image.spread, image.index) for image in images}
        assert got == expected_from_manifest(truth.pages), map_dir.name
        for region in truth.regions:
            assert region.image in got, (map_dir.name, region.id)
