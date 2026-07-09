from pathlib import Path

import pytest

from conftest import fabricate_workdir
from osrforge.pages import clamp_pages, page_request_parts
from osrforge.providers.base import ImagePart, TextPart


def test_parts_interleave_text_then_image_in_given_order(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=3)
    parts = page_request_parts(workdir, [3, 1])
    assert len(parts) == 4
    assert isinstance(parts[0], TextPart) and parts[0].text == "[page 3]\ntext of page 3\n"
    assert isinstance(parts[1], ImagePart) and parts[1].png == b"png-3"
    assert isinstance(parts[2], TextPart) and parts[2].text == "[page 1]\ntext of page 1\n"
    assert isinstance(parts[3], ImagePart) and parts[3].png == b"png-1"


def test_page_marker_emitted_for_empty_text_layer(tmp_path: Path):
    # The scanned-module path: an empty text layer still gets its marker —
    # the markers define the page-number space.
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    workdir.page_txt(1).write_text("", encoding="utf-8")
    parts = page_request_parts(workdir, [1])
    assert isinstance(parts[0], TextPart) and parts[0].text == "[page 1]\n"


def test_missing_page_raises_value_error(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=2)
    with pytest.raises(ValueError, match="page 3"):
        page_request_parts(workdir, [1, 3])
    workdir.page_txt(2).unlink()
    with pytest.raises(ValueError, match="page 2"):
        page_request_parts(workdir, [2])


def test_clamp_pages_drops_dedupes_and_sorts():
    assert clamp_pages([3, 50, 3, 1, 0, -2], page_count=10) == (1, 3)
    assert clamp_pages([], page_count=10) == ()
    assert clamp_pages([10, 1], page_count=10) == (1, 10)
