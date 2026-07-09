from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, write_json_artifact


def test_layout_paths():
    workdir = Workdir(Path("my-module.forge"))
    assert workdir.source_pdf == Path("my-module.forge/source.pdf")
    assert workdir.run_json == Path("my-module.forge/run.json")
    assert workdir.pages_dir == Path("my-module.forge/pages")
    assert workdir.stages_dir == Path("my-module.forge/stages")
    assert workdir.overrides_yaml == Path("my-module.forge/overrides.yaml")
    assert workdir.previews_dir == Path("my-module.forge/previews")
    assert workdir.report_json == Path("my-module.forge/report.json")
    assert workdir.adventure_json == Path("my-module.forge/adventure.json")
    assert workdir.page_png(7) == Path("my-module.forge/pages/0007.png")
    assert workdir.page_txt(123) == Path("my-module.forge/pages/0123.txt")


def test_pinned_json_writer_format(tmp_path: Path):
    class Sample(BaseModel):
        zebra: str
        apple: int

    path = tmp_path / "artifact.json"
    write_json_artifact(path, Sample(zebra="stripes — ünïcödé", apple=1))
    text = path.read_text(encoding="utf-8")
    # Model-declaration order (no sorting), 2-space indent, real UTF-8 (no
    # \u escapes), trailing newline.
    assert text == '{\n  "zebra": "stripes — ünïcödé",\n  "apple": 1\n}\n'


def test_pinned_json_writer_accepts_mappings(tmp_path: Path):
    path = tmp_path / "artifact.json"
    write_json_artifact(path, {"kind": "adventure", "payload": {"name": "x"}})
    assert path.read_text(encoding="utf-8").endswith("}\n")


def test_writer_bytes_are_stable(tmp_path: Path):
    settings = ConversionSettings()
    write_json_artifact(tmp_path / "a.json", settings)
    write_json_artifact(tmp_path / "b.json", settings)
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_settings_are_frozen_and_strict():
    settings = ConversionSettings()
    assert settings.render_dpi == 150
    assert settings.max_pages == 200
    assert settings.max_source_bytes == 100 * 1024 * 1024
    with pytest.raises(ValidationError):
        ConversionSettings.model_validate({"render_api": 300})  # typo'd knob is rejected, not ignored
