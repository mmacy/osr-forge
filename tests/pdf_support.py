"""Build PDFs with embedded images for the map-image tests, from a page manifest or from placements.

A placement is one image drawn on one page at a rectangle in points, with the page's origin at the bottom left as
PDF has it. A rectangle may run off the page: that's how a two-page spread is drawn, with the image placed across
the fold so each page shows one half.
"""

import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from osrforge.tracing.truth import Page

PAGE_WIDTH = 200.0
PAGE_HEIGHT = 300.0
FORM_SIZE = 100.0


@dataclass(frozen=True)
class Placement:
    """One image drawn on one page.

    Attributes:
        page: The 1-based page number.
        image: The pixels.
        rect: `(left, bottom, right, top)` in points; may run off the page.
        jpeg: Store the image as JPEG at quality 95 instead of as raw pixels.
        form: Draw the image inside a form object that fills the rectangle, so the image's own bounds are in
            the form's space and the form's matrix places it on the page.
    """

    page: int
    image: Image.Image
    rect: tuple[float, float, float, float]
    jpeg: bool = False
    form: bool = False


def build_pdf(path: Path, placements: Sequence[Placement], page_count: int | None = None) -> Path:
    """Write a PDF of blank pages with the placements drawn on them, in the order given per page."""
    last = max((placement.page for placement in placements), default=0)
    count = page_count if page_count is not None else last
    # pypdfium2's PDF-writing API has no type stubs; the same ignores sit in test_map_images.py.
    pdf = pdfium.PdfDocument.new()  # pyright: ignore[reportAttributeAccessIssue]
    pages = [pdf.new_page(PAGE_WIDTH, PAGE_HEIGHT) for _ in range(count)]
    sources = []
    for placement in placements:
        page = pages[placement.page - 1]
        left, bottom, right, top = placement.rect
        if placement.form:
            # A one-page source document whose page the image fills becomes a form object on the target page.
            source = pdfium.PdfDocument.new()  # pyright: ignore[reportAttributeAccessIssue]
            sources.append(source)
            inner = source.new_page(FORM_SIZE, FORM_SIZE)
            image = pdfium.PdfImage.new(source)  # pyright: ignore[reportAttributeAccessIssue]
            image.set_bitmap(pdfium.PdfBitmap.from_pil(placement.image.convert("RGB")))  # pyright: ignore[reportAttributeAccessIssue]
            image.set_matrix(pdfium.PdfMatrix().scale(FORM_SIZE, FORM_SIZE))  # pyright: ignore[reportAttributeAccessIssue]
            inner.insert_obj(image)
            inner.gen_content()
            form = source.page_as_xobject(0, pdf).as_pageobject()
            form.set_matrix(
                pdfium.PdfMatrix()  # pyright: ignore[reportAttributeAccessIssue]
                .scale((right - left) / FORM_SIZE, (top - bottom) / FORM_SIZE)
                .translate(left, bottom)
            )
            page.insert_obj(form)
            continue
        image = pdfium.PdfImage.new(pdf)  # pyright: ignore[reportAttributeAccessIssue]
        if placement.jpeg:
            buffer = io.BytesIO()
            placement.image.convert("RGB").save(buffer, "JPEG", quality=95)
            buffer.seek(0)
            image.load_jpeg(buffer)
        else:
            # pdfium stores a 1-bit or L source as 8-bit grey and a P or CMYK source as 24-bit RGB, so the
            # builder can't make a true 1-bit, indexed, or CMYK image object.
            image.set_bitmap(pdfium.PdfBitmap.from_pil(placement.image))  # pyright: ignore[reportAttributeAccessIssue]
        image.set_matrix(pdfium.PdfMatrix().scale(right - left, top - bottom).translate(left, bottom))  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_obj(image)
    for page in pages:
        page.gen_content()
    pdf.save(path)
    return path


def placements_from_manifest(pages: Sequence[Page], images: Mapping[str, Image.Image]) -> list[Placement]:
    """Turn a page manifest into placements.

    Each page's images are drawn in manifest order. A hidden image gets the same rectangle as the next image on
    its page, which covers it. The other images on a page get bands of equal height from the top. A spread image
    is listed on both of its pages, each naming the other in `spread_with`, and is drawn across the fold, twice
    the page width, left half on the lower-numbered page and right half on the higher.
    """
    by_number = {page.number: page for page in pages}
    placements: list[Placement] = []
    for page in pages:
        partner = by_number.get(page.spread_with) if page.spread_with is not None else None
        partner_files = {image.file for image in partner.images} if partner is not None else set()
        if page.spread_with is not None:
            assert partner is not None and partner.spread_with == page.number, f"page {page.number}: spread_with"
            assert partner_files & {image.file for image in page.images}, f"page {page.number}: no spread image"
        groups: list[list[int]] = []
        pending: list[int] = []
        for i, image in enumerate(page.images):
            pending.append(i)
            if not image.hidden:
                groups.append(pending)
                pending = []
        assert not pending, f"page {page.number}: a hidden image needs a later image that covers it"
        band = PAGE_HEIGHT / max(len(groups), 1)
        for group_index, group in enumerate(groups):
            top = PAGE_HEIGHT - group_index * band
            for i in group:
                image = page.images[i]
                if image.file in partner_files and partner is not None:
                    left = 0.0 if page.number < partner.number else -PAGE_WIDTH
                    rect = (left, top - band, left + 2 * PAGE_WIDTH, top)
                else:
                    rect = (0.0, top - band, PAGE_WIDTH, top)
                placements.append(Placement(page=page.number, image=images[image.file], rect=rect))
    return placements


def build_pdf_from_manifest(path: Path, pages: Sequence[Page], images: Mapping[str, Image.Image]) -> Path:
    """Write a PDF whose pages carry the manifest's images, including those on blanked pages."""
    return build_pdf(path, placements_from_manifest(pages, images), page_count=max(page.number for page in pages))
