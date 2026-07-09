import hashlib
from pathlib import Path

import pytest
from PIL import Image

from osrforge.contracts.run import Stage
from osrforge.errors import PdfError
from osrforge.preprocess import preprocess
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

MINIMOD_PAGES = 5
IMAGE_ONLY_PAGE = 4
LETTER_POINTS = (612, 792)


@pytest.fixture
def workdir_path(tmp_path: Path) -> Path:
    return tmp_path / "minimod.forge"


def test_page_count_and_numbering(minimod_pdf: Path, workdir_path: Path):
    run = preprocess(minimod_pdf, workdir_path, ConversionSettings())
    assert run.page_count == MINIMOD_PAGES
    pages = Workdir(workdir_path).pages_dir
    assert sorted(p.name for p in pages.glob("*.png")) == [f"{n:04d}.png" for n in range(1, MINIMOD_PAGES + 1)]
    assert sorted(p.name for p in pages.glob("*.txt")) == [f"{n:04d}.txt" for n in range(1, MINIMOD_PAGES + 1)]


def test_text_page_contains_expected_strings(minimod_pdf: Path, workdir_path: Path):
    preprocess(minimod_pdf, workdir_path, ConversionSettings())
    workdir = Workdir(workdir_path)
    assert "The Root Cellar of Old Wenna" in workdir.page_txt(1).read_text(encoding="utf-8")
    page3 = workdir.page_txt(3).read_text(encoding="utf-8")
    assert "goblin" in page3
    assert "30' x 40'" in page3
    assert "\r" not in page3


def test_image_only_page_yields_empty_text_file(minimod_pdf: Path, workdir_path: Path):
    preprocess(minimod_pdf, workdir_path, ConversionSettings())
    text_file = Workdir(workdir_path).page_txt(IMAGE_ONLY_PAGE)
    assert text_file.exists()
    assert text_file.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("dpi", [100, 150])
def test_png_dimensions_match_dpi(minimod_pdf: Path, tmp_path: Path, dpi: int):
    workdir_path = tmp_path / f"minimod-{dpi}.forge"
    preprocess(minimod_pdf, workdir_path, ConversionSettings(render_dpi=dpi))
    with Image.open(Workdir(workdir_path).page_png(1)) as image:
        for actual, points in zip(image.size, LETTER_POINTS, strict=True):
            assert abs(actual - points * dpi / 72) <= 1


def test_max_pages_limit_raises(minimod_pdf: Path, workdir_path: Path):
    with pytest.raises(PdfError, match="page"):
        preprocess(minimod_pdf, workdir_path, ConversionSettings(max_pages=1))


def test_max_source_bytes_limit_raises(minimod_pdf: Path, workdir_path: Path):
    with pytest.raises(PdfError, match="byte"):
        preprocess(minimod_pdf, workdir_path, ConversionSettings(max_source_bytes=100))


def test_encrypted_source_raises(encrypted_pdf: Path, workdir_path: Path):
    with pytest.raises(PdfError, match="password"):
        preprocess(encrypted_pdf, workdir_path, ConversionSettings())


def test_missing_source_raises(tmp_path: Path, workdir_path: Path):
    with pytest.raises(PdfError):
        preprocess(tmp_path / "nope.pdf", workdir_path, ConversionSettings())


def test_rerun_clears_stale_pages(minimod_pdf: Path, workdir_path: Path):
    preprocess(minimod_pdf, workdir_path, ConversionSettings())
    pages = Workdir(workdir_path).pages_dir
    stale_png = pages / "0099.png"
    stale_png.write_bytes(b"stale")
    (pages / "0099.txt").write_text("stale", encoding="utf-8")

    preprocess(minimod_pdf, workdir_path, ConversionSettings())
    assert not stale_png.exists()
    assert len(list(pages.iterdir())) == MINIMOD_PAGES * 2


def test_run_json_contents(minimod_pdf: Path, workdir_path: Path):
    settings = ConversionSettings(render_dpi=120)
    returned = preprocess(minimod_pdf, workdir_path, settings)
    workdir = Workdir(workdir_path)
    run = workdir.read_run()
    assert run == returned
    assert run.source_sha256 == hashlib.sha256(minimod_pdf.read_bytes()).hexdigest()
    assert run.source_bytes == minimod_pdf.stat().st_size
    assert run.page_count == MINIMOD_PAGES
    assert run.settings == settings
    assert run.provider is None and run.model_id is None
    assert run.stages[Stage.PREPROCESS].status == "completed"
    assert run.stages[Stage.PREPROCESS].started_at is not None
    for stage in Stage:
        if stage is not Stage.PREPROCESS:
            assert run.stages[stage].status == "pending"


def test_source_copied_into_workdir(minimod_pdf: Path, workdir_path: Path):
    preprocess(minimod_pdf, workdir_path, ConversionSettings())
    copied = Workdir(workdir_path).source_pdf
    assert copied.read_bytes() == minimod_pdf.read_bytes()
