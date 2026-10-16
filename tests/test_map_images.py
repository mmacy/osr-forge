"""The committed native map images of the freely licensed corpus members.

The tracing stage reads a map from the image the PDF embeds, at the resolution the PDF stores it at. Registration's
tests run on real maps, so the native map images of JN1 The Chaotic Caves and JN2 The Monkey Isle are committed
under CC BY-SA 4.0, each member's in a `maps/` directory with a manifest, `maps.json`:

```json
{
  "source_sha256": "<the PDF's sha256, as the corpus manifest pins it>",
  "images": [
    {
      "file": "p038.png",
      "page": 38,
      "image": 0,
      "width": 1874,
      "height": 2450,
      "sha256": "<the committed file's sha256>",
      "levels": [["A. Orc Lair", 1]]
    }
  ],
  "no_map": [["Crypt", 1]]
}
```

`page` is the PDF's 1-based page number. `image` is the index of the image among that page's image objects in
pypdfium2's order, counting into form objects. `levels` names the corpus truth's dungeon and level numbers the image
draws, and `no_map` names the levels the module prints no map for. The PDFs aren't committed. When a member's PDF is
present at its local path, the re-extraction test checks every committed image against it pixel for pixel.
"""

import hashlib
import json
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
import yaml
from PIL import Image

pytestmark = pytest.mark.xfail(reason="chunk: map-images")

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "tests" / "assets"
CORPUS = ROOT / "tools" / "eval" / "corpus"

MEMBERS = {
    "chaotic-caves": {
        "corpus": "jn1-chaotic-caves",
        "pdf": ASSETS / "chaotic-caves" / "JN1-Chaotic-Caves-r28.pdf",
        "pages": {21, 38, 46, 47},
        "no_map": {("Crypt", 1)},
    },
    "monkey-isle": {
        "corpus": "jn2-monkey-isle",
        "pdf": ASSETS / "monkey-isle" / "JN2-Monkey-Isle-r22.pdf",
        "pages": None,
        "no_map": set(),
    },
}

SIZE_LIMIT_BYTES = 12_000_000


def manifest(member: str) -> dict:
    return json.loads((ASSETS / member / "maps" / "maps.json").read_text(encoding="utf-8"))


def corpus_levels(member: str) -> set[tuple[str, int]]:
    truth = yaml.safe_load((CORPUS / MEMBERS[member]["corpus"] / "truth.yaml").read_text(encoding="utf-8"))
    return {(dungeon["name"], level["number"]) for dungeon in truth["dungeons"] for level in dungeon["levels"]}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("member", sorted(MEMBERS))
def test_manifest_names_the_corpus_source(member):
    pinned = yaml.safe_load((CORPUS / MEMBERS[member]["corpus"] / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest(member)["source_sha256"] == pinned["sha256"]


@pytest.mark.parametrize("member", sorted(MEMBERS))
def test_every_listed_image_is_committed_as_described(member):
    images = manifest(member)["images"]
    assert images, "a member's manifest lists at least one image"
    for entry in images:
        path = ASSETS / member / "maps" / entry["file"]
        assert path.is_file(), entry["file"]
        assert sha256(path) == entry["sha256"], entry["file"]
        with Image.open(path) as image:
            assert image.size == (entry["width"], entry["height"]), entry["file"]


@pytest.mark.parametrize("member", sorted(MEMBERS))
def test_only_listed_images_are_committed(member):
    listed = {entry["file"] for entry in manifest(member)["images"]}
    present = {path.name for path in (ASSETS / member / "maps").iterdir() if path.name != "maps.json"}
    assert present == listed


@pytest.mark.parametrize("member", sorted(MEMBERS))
def test_every_corpus_level_has_its_map_or_none(member):
    data = manifest(member)
    named = [tuple(level) for entry in data["images"] for level in entry["levels"]]
    no_map = {tuple(level) for level in data["no_map"]}
    assert len(named) == len(set(named)), "each level is drawn by one listed image"
    assert no_map == MEMBERS[member]["no_map"]
    assert set(named) | no_map == corpus_levels(member)
    assert not set(named) & no_map


def test_chaotic_caves_maps_are_the_printed_map_pages():
    assert {entry["page"] for entry in manifest("chaotic-caves")["images"]} == MEMBERS["chaotic-caves"]["pages"]


@pytest.mark.parametrize("member", sorted(MEMBERS))
def test_committed_images_match_a_fresh_extraction(member):
    pdf_path = MEMBERS[member]["pdf"]
    if not pdf_path.is_file():
        pytest.skip(f"{pdf_path.name} isn't present locally")
    assert sha256(pdf_path) == manifest(member)["source_sha256"]
    pdf = pdfium.PdfDocument(pdf_path)
    for entry in manifest(member)["images"]:
        page = pdf[entry["page"] - 1]
        # pypdfium2 defines get_objects in a way pyright can't see.
        objects = list(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3))  # pyright: ignore[reportAttributeAccessIssue]
        extracted = objects[entry["image"]].get_bitmap(render=False).to_pil()
        with Image.open(ASSETS / member / "maps" / entry["file"]) as committed:
            assert committed.size == extracted.size, entry["file"]
            assert committed.convert(extracted.mode).tobytes() == extracted.tobytes(), entry["file"]


def test_committed_map_images_stay_within_the_size_limit():
    total = sum(path.stat().st_size for member in MEMBERS for path in (ASSETS / member / "maps").iterdir())
    assert total <= SIZE_LIMIT_BYTES


def test_licensing_attributes_the_map_images():
    licensing = (ROOT / "docs" / "licensing.md").read_text(encoding="utf-8")
    for member in MEMBERS:
        assert f"tests/assets/{member}/maps/" in licensing
    assert "CC BY-SA 4.0" in licensing
    assert "J.D. Neal" in licensing
