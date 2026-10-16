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

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["HIDDEN_COVERAGE", "MapImage", "extract_map_images", "map_image_filename"]

HIDDEN_COVERAGE = 0.5
"""An image is hidden when one later image on its page covers more than this share of its placement rectangle.

Each later image counts alone: two later images that together cover an
earlier one, neither of them by more than this share, hide nothing.
"""


class MapImage(BaseModel):
    """One image the stage keeps, written as PNG under the workdir's `maps/` directory.

    Attributes:
        file: The PNG's name, `p<page>-<index>.png` with the page zero-padded
            to four digits, from the first page the image appears on.
        pages: The listed pages the image appears on, ascending: one page
            for an image printed once, two for a spread, and as many as it's
            printed on for an image printed on several pages.
        index: The image's index among its first page's image objects, in
            pypdfium2's drawing order counting into form objects, hidden
            images included.
        width: The image's width in pixels, as stored.
        height: The image's height in pixels, as stored.
        sha256: The sha256 of the PNG file as written. It describes the
            written file, so it depends on the PNG encoder, not only on the
            pixels.
        spread: True when one image spans a two-page spread: exactly two
            consecutive pages, the first page's placement running off its
            right edge and the second page's off its left edge.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    pages: tuple[int, ...] = Field(min_length=1)
    index: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: str
    spread: bool = False

    @model_validator(mode="after")
    def _pages_ascend_and_a_spread_is_two_consecutive_pages(self) -> MapImage:
        if list(self.pages) != sorted(set(self.pages)):
            raise ValueError(f"pages ascend without repeats: {self.pages}")
        if self.spread and (len(self.pages) != 2 or self.pages[1] != self.pages[0] + 1):
            raise ValueError(f"a spread is exactly two consecutive pages: {self.pages}")
        return self


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

    The listed pages are taken in ascending order, whatever order they're
    given in, and an image's first page is the lowest listed page it's on.
    Every image object on each listed page is read with pypdfium2, in drawing
    order counting into form objects, as the pixels the PDF stores
    (`get_bitmap(render=False)`), converted to RGB. A page in `blanked` is
    skipped: nothing on it is read or matched. The rules then run in this
    order on each page's images, and then across pages:

    1. **Hiding.** An image's placement is the quadrilateral its unit square
       maps to in page coordinates, through its own matrix and the matrix of
       each form object it's inside. Its placement rectangle is that
       quadrilateral's axis-aligned bounds, and its clipped rectangle is the
       placement rectangle clipped to the page. An image whose clipped
       rectangle has no area is off the page: it's never written, though it
       keeps its place in the index count. An image is hidden, and left out,
       when one later image on the same page overlaps more than
       [`HIDDEN_COVERAGE`][osrforge.tracing.images.HIDDEN_COVERAGE] of its
       clipped rectangle's area, the overlap being the intersection of the
       two clipped rectangles. Each later image counts alone, never the
       union of several. The same pixels drawn twice on one page, the second
       over the first, hide the first this way, and the second is kept.
    2. **Duplicates.** Two images have the same pixels when their pixels are
       equal after conversion to RGB. Among the images hiding left, those
       with the same pixels on several listed pages are one image, listed
       once with every such page, under the first page's name and index. An
       image hidden on one page and shown alone on another is that other
       page's image. The same pixels twice on one page, neither hidden,
       count once, under the lower index.
    3. **Spreads.** A duplicate on exactly two consecutive pages spans a
       spread when its placement rectangles run off facing edges, unclipped:
       on the first page, some copy's rectangle runs off the right edge, and
       on the second, some copy's runs off the left edge. Identical images
       on consecutive pages whose placements don't both run off those edges
       are a plain duplicate.

    Each kept image is written to `out_dir / file` as an RGB PNG. The
    directory is created if missing. The function overwrites each file it
    writes and leaves every other file in the directory untouched.

    Args:
        pdf_path: The PDF.
        pages: The 1-based pages to read: the survey's map pages.
        blanked: The pages the `blank_page_renders` setting blanks.
        out_dir: Where the PNGs go.

    Returns:
        The kept images, in order of first page and then index.

    Raises:
        PdfError: If the PDF can't be opened.
        ValueError: If a listed page is below 1 or beyond the PDF's page
            count, whether or not it's blanked.
    """
    raise NotImplementedError("chunk: map-images")
