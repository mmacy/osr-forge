"""The map images: each image a map page embeds, at the resolution the PDF stores it at.

The tracing stage's first step. Printed maps are pictures: in the corpus
survey, 114 of 115 map pages are raster images, so the stage reads each map
from the image the PDF embeds, not from a render. This module extracts those
images with pypdfium2 and handles the four ways a page can mislead: one image
drawn across a two-page spread, the same image printed on two pages, an
image drawn over an earlier one, and a page the `blank_page_renders` setting
blanks because a model service's content filter rejected it.
"""

from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["HIDDEN_COVERAGE", "MapImage", "extract_map_images", "map_image_filename"]

HIDDEN_COVERAGE = 0.5
"""An image is hidden when a later image on its page covers more than this share of its placement."""


class MapImage(BaseModel):
    """One image the stage keeps, written as PNG under the workdir's `maps/` directory.

    Attributes:
        file: The PNG's name, `p<page>-<index>.png` with the page zero-padded
            to four digits, from the first page the image appears on.
        pages: The listed pages the image appears on, ascending: one page,
            or two for a spread or an image printed twice.
        index: The image's index among its first page's image objects, in
            pypdfium2's drawing order counting into form objects, hidden
            images included.
        width: The image's width in pixels, as stored.
        height: The image's height in pixels, as stored.
        sha256: The PNG file's sha256.
        spread: True when one image spans a two-page spread: the first page's
            placement runs off its right edge and the next page's off its
            left edge.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    pages: tuple[int, ...] = Field(min_length=1, max_length=2)
    index: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: str
    spread: bool = False


def map_image_filename(page: int, index: int) -> str:
    """Return the file name of a map image: `p<page>-<index>.png`.

    Args:
        page: The 1-based page number.
        index: The image's index on that page.

    Returns:
        The file name.
    """
    return f"p{page:04d}-{index}.png"


def extract_map_images(
    pdf_path: Path,
    pages: Iterable[int],
    blanked: Iterable[int],
    out_dir: Path,
) -> tuple[MapImage, ...]:
    """Extract the images embedded on a PDF's map pages, at native resolution, and write each as PNG.

    Every image object on each listed page is read with pypdfium2, in
    drawing order counting into form objects, as the pixels the PDF stores
    (`get_bitmap(render=False)`), converted to RGB. Then:

    - A page in `blanked` is skipped: nothing on it is read or matched.
    - An image is hidden, and left out, when a later image on the same page
      covers more than [`HIDDEN_COVERAGE`][osrforge.tracing.images.HIDDEN_COVERAGE]
      of its placement rectangle on the page, because the later one is the
      one the page shows.
    - Two images with the same pixels on two listed pages are one image,
      listed once with both pages, under the first page's name. On
      consecutive pages where the first page's placement runs off its right
      edge and the second's off its left edge, the image spans a spread. The
      same pixels twice on one page count once.

    Each kept image is written to `out_dir / file` as PNG. The directory is
    created if missing, and files it already holds are left alone.

    Args:
        pdf_path: The PDF.
        pages: The 1-based pages to read: the survey's map pages.
        blanked: The pages the `blank_page_renders` setting blanks.
        out_dir: Where the PNGs go.

    Returns:
        The kept images, in order of first page and then index.

    Raises:
        PdfError: If the PDF can't be opened, or a listed page is beyond its
            page count.
    """
    raise NotImplementedError("chunk: map-images")
