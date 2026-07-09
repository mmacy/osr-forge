# /// script
# requires-python = ">=3.14"
# dependencies = ["reportlab>=4", "pillow>=11"]
# ///
"""Generate the CC0 minimod test assets: tests/assets/minimod/minimod.pdf and encrypted.pdf.

Run from the repo root:

    uv run tools/minimod/generate_minimod.py

The module is an original mini-adventure authored for this repo and dedicated
to the public domain (CC0 1.0). Pages 1-3 and 5 carry a text layer; page 4 is a
raster map image with no text objects, exercising the scanned-module path.
"""

import io
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ASSET_DIR = Path(__file__).resolve().parents[2] / "tests" / "assets" / "minimod"

PAGE_WIDTH, PAGE_HEIGHT = letter


def draw_text_page(pdf: canvas.Canvas, title: str, lines: list[str]) -> None:
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawString(72, PAGE_HEIGHT - 84, title)
    pdf.setFont("Helvetica", 11)
    y = PAGE_HEIGHT - 120
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 16
    pdf.showPage()


def make_map_image() -> Image.Image:
    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    rooms = [(40, 40, 160, 160), (220, 40, 340, 120), (400, 40, 480, 160), (40, 240, 200, 400), (300, 240, 480, 460)]
    for left, top, right, bottom in rooms:
        draw.rectangle((left, top, right, bottom), outline="black", width=4)
    corridors = [(160, 90, 220, 100), (340, 80, 400, 90), (100, 160, 110, 240), (200, 320, 300, 330)]
    for left, top, right, bottom in corridors:
        draw.rectangle((left, top, right, bottom), fill="black")
    return image


def write_minimod(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    pdf.setTitle("The Root Cellar of Old Wenna")

    draw_text_page(
        pdf,
        "The Root Cellar of Old Wenna",
        [
            "A one-evening dungeon crawl for characters of level 1.",
            "",
            "An original mini-module authored as a test asset for osr-forge.",
            "To the extent possible under law, the author has dedicated this",
            "work to the public domain under CC0 1.0. No rights reserved.",
        ],
    )

    draw_text_page(
        pdf,
        "Background and the village of Wennadale",
        [
            "Old Wenna's farmhouse burned down a generation ago, but her",
            "famous root cellar survives beneath the ruin, and lately things",
            "have been crawling out of it at night.",
            "",
            "Wennadale is a village of forty souls. The Dry Barrel inn offers",
            "beds and rumors; Maro the peddler sells torches, rope, and oil.",
            "The cellar mouth lies one mile east of the village green.",
        ],
    )

    draw_text_page(
        pdf,
        "The cellar, level 1: areas 1 to 4",
        [
            "1. Collapsed stair. A 20' x 30' chamber of packed earth. Broken",
            "   steps descend from the surface. 2 giant rats nest in the",
            "   rubble and attack anyone bearing a light.",
            "2. Turnip store. A 30' x 40' vault lined with rotted bins. A",
            "   goblin scavenger named Snagg picks through the bins and will",
            "   parley if offered food. A corridor continues north to area 3.",
            "3. Cider press. A 20' x 20' room dominated by a ruined press.",
            "   Under the press bed is a loose flagstone hiding 120 sp and a",
            "   silver locket worth 25 gp.",
            "4. Flooded gallery. A 40' x 10' gallery ankle-deep in seep",
            "   water. 4 giant centipedes cling to the ceiling beams. A stuck",
            "   door in the east wall leads to area 5.",
        ],
    )

    map_page = make_map_image()
    buffer = io.BytesIO()
    map_page.save(buffer, format="PNG")
    buffer.seek(0)
    pdf.drawImage(ImageReader(buffer), 72, PAGE_HEIGHT - 72 - 468, width=468, height=468)
    pdf.showPage()

    draw_text_page(
        pdf,
        "The cellar, level 1: areas 5 to 6",
        [
            "5. Wenna's pantry. A 30' x 30' room, shelves still stocked with",
            "   preserves. A skeleton in a scorched apron guards the room,",
            "   attacking anyone who touches the shelves.",
            "6. The deep bin. A 10' x 10' pit-room reached by ladder. 3",
            "   stirges roost above a heap of grain sacks. Buried in the",
            "   grain: a pot of 200 gp and a potion of healing.",
            "",
            "Wandering monsters (1 in 6, check every 2 turns): 1-2 giant",
            "rats (1d4), 3-4 goblin (1d2), 5-6 giant centipedes (1d2).",
        ],
    )

    pdf.save()


def write_encrypted(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter, encrypt="minimod-owner-password")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, PAGE_HEIGHT - 84, "This page exists only to exercise the encrypted-source error path.")
    pdf.showPage()
    pdf.save()


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    minimod = ASSET_DIR / "minimod.pdf"
    encrypted = ASSET_DIR / "encrypted.pdf"
    write_minimod(minimod)
    write_encrypted(encrypted)
    for path in (minimod, encrypted):
        print(f"{path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
