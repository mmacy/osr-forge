"""Stage 0: deterministic PDF preprocessing.

Copies the source into the workdir, renders every page to PNG, extracts every
page's text layer, and writes `run.json`. No model calls — everything here is
deterministic code, though PNG byte-stability across pdfium/Pillow versions is
explicitly not a contract (assembly purity begins at the cached stage outputs).
"""

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pypdfium2 as pdfium

from osrforge.contracts.run import RunMeta, Stage, StageStatus
from osrforge.errors import PdfError
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

__all__ = ["preprocess"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _render_pages(pdf: pdfium.PdfDocument, workdir: Workdir, settings: ConversionSettings) -> int:
    scale = settings.render_dpi / 72
    page_count = len(pdf)
    for index in range(page_count):
        page = pdf[index]
        try:
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            image.save(workdir.page_png(index + 1))
            textpage = page.get_textpage()
            try:
                # get_text_bounded is the fully-Unicode API; pypdfium2 documents
                # get_text_range as UCS-2-limited, and module text can carry
                # non-BMP glyphs. An empty or missing text layer yields an empty
                # file — the scanned-module behavior — never a skipped one.
                text = textpage.get_text_bounded()
            finally:
                textpage.close()
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            workdir.page_txt(index + 1).write_text(text, encoding="utf-8")
        finally:
            page.close()
    return page_count


def preprocess(pdf_path: Path, workdir_path: Path, settings: ConversionSettings) -> RunMeta:
    """Run stage 0: copy, render, and extract the source module into a workdir.

    Re-running on an existing workdir rebuilds it — `pages/` is cleared before
    rendering so a shorter re-render never leaves stale trailing pages.
    Skip-if-unchanged logic belongs to `rerun` (phase 3), not here.

    Args:
        pdf_path: The source module PDF.
        workdir_path: The workdir root to create or rebuild.
        settings: The pipeline settings; the full settings echo lands in `run.json`.

    Returns:
        The run metadata, as written to `run.json`: preprocess `completed`, all
        other stages `pending`.

    Raises:
        PdfError: If the source is missing, over a configured limit, corrupt,
            or password-protected (encrypted PDFs are unsupported in v1).
    """
    if not pdf_path.is_file():
        raise PdfError(f"source is not a readable file: {pdf_path}")
    source_bytes = pdf_path.stat().st_size
    if source_bytes > settings.max_source_bytes:
        raise PdfError(f"source is {source_bytes} bytes, over the {settings.max_source_bytes}-byte limit: {pdf_path}")

    workdir = Workdir(workdir_path)
    workdir.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf_path, workdir.source_pdf)
    source_sha256 = _sha256(workdir.source_pdf)

    started_at = datetime.now(UTC)
    try:
        pdf = pdfium.PdfDocument(workdir.source_pdf)
    except pdfium.PdfiumError as error:
        raise PdfError(f"source is corrupt or password-protected: {pdf_path}") from error
    try:
        page_count = len(pdf)
        if page_count > settings.max_pages:
            raise PdfError(f"source has {page_count} pages, over the {settings.max_pages}-page limit: {pdf_path}")
        if workdir.pages_dir.exists():
            shutil.rmtree(workdir.pages_dir)
        workdir.pages_dir.mkdir()
        _render_pages(pdf, workdir, settings)
    finally:
        pdf.close()

    stages = {stage: StageStatus() for stage in Stage}
    stages[Stage.PREPROCESS] = StageStatus(status="completed", started_at=started_at, finished_at=datetime.now(UTC))
    run = RunMeta(
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        page_count=page_count,
        settings=settings,
        stages=stages,
    )
    workdir.write_run(run)
    return run
