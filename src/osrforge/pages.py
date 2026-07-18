"""The shared interleaved text-plus-image request-part builder.

Including the text layer is load-bearing, not a nicety: in the recorded
capability probes, an images-only survey request produced one dungeon, three
areas, and one monster name, while the text-plus-image request over the same
module's pages produced the full index. Per-page interleaving is the pinned
arrangement — the shape every successful extraction probe used. Every
page-consuming request — survey, content, and the stat-block pass — builds
its parts only through this function.

This lives in its own module because `Workdir`'s charter is layout plus
`run.json` I/O, `providers/` is the vendor seam, and both stages need it — a
one-function module matches house scale (`versioning.py`).
"""

from collections.abc import Iterable, Sequence

from osrforge.providers.base import ImagePart, TextPart
from osrforge.workdir import Workdir

__all__ = ["clamp_pages", "page_request_parts"]


def clamp_pages(pages: Iterable[int], page_count: int) -> tuple[int, ...]:
    """Normalize a model-supplied page list: drop out-of-range references, deduplicate, sort.

    Models hallucinate page references; anything outside 1..page_count is
    dropped. Lives here — not in a stage module — because both extraction
    stages normalize page lists and no stage module imports another.

    Args:
        pages: The page numbers as the model gave them.
        page_count: The source's page count.

    Returns:
        The in-range pages, deduplicated and ascending.
    """
    return tuple(sorted({page for page in pages if 1 <= page <= page_count}))


def page_request_parts(workdir: Workdir, page_numbers: Sequence[int]) -> tuple[TextPart | ImagePart, ...]:
    r"""Build the ordered request parts for a sequence of pages.

    For each page, in the given order: a `TextPart` of `"[page N]\n"` plus the
    page's extracted text, immediately followed by the page's `ImagePart`. The
    `[page N]` marker is emitted even when the text layer is empty (the
    scanned-module path) — the markers *define the page-number space*: printed
    page numbers visible in the images differ from PDF page numbers, so every
    prompt states that `source_pages`/`map_pages` refer to these markers.

    Args:
        workdir: The workdir holding `pages/NNNN.png` and `pages/NNNN.txt`.
        page_numbers: The 1-based page numbers, in request order.

    Returns:
        The interleaved text and image parts.

    Raises:
        ValueError: If a page's render or text file is missing (misuse:
            preprocess didn't run, or a bad page number).
    """
    parts: list[TextPart | ImagePart] = []
    for number in page_numbers:
        png_path = workdir.page_png(number)
        txt_path = workdir.page_txt(number)
        if not png_path.is_file() or not txt_path.is_file():
            raise ValueError(f"page {number} is missing from {workdir.pages_dir} — preprocess the source first")
        text = txt_path.read_text(encoding="utf-8")
        parts.append(TextPart(text=f"[page {number}]\n{text}"))
        parts.append(ImagePart(png=png_path.read_bytes()))
    return tuple(parts)
