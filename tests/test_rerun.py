"""`rerun`: resume from any stage, the settings channel, and the drift guard.

Reruns from the model stages replay over the minimod fixture workdir — its
pages are the committed renders, so the request fingerprints hold. The
rerun-from-preprocess path uses a scripted stub provider only: re-rendering
produces fresh PNG bytes, and pipeline tests never fingerprint fresh renders
(a FixtureProvider rerun from preprocess would miss on at least one CI OS).
"""

from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from conftest import ScriptedProvider
from osrforge.contracts.run import Stage
from osrforge.convert import RUNNABLE_STAGES, StageEvent, rerun
from osrforge.errors import PdfError, ProviderError
from osrforge.preprocess import preprocess
from osrforge.providers.fixtures import FixtureProvider
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir
from test_convert import FailAfter
from test_minimod_pipeline import MINIMOD, golden_files, run_pipeline
from test_overrides_apply import synthetic_workdir


def produced_artifacts(root: Path) -> dict[str, bytes]:
    workdir = Workdir(root)
    produced = {f"stages/{path.name}": path.read_bytes() for path in sorted(workdir.stages_dir.iterdir())}
    produced |= {f"previews/{path.name}": path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    produced["adventure.json"] = workdir.adventure_json.read_bytes()
    produced["report.json"] = workdir.report_json.read_bytes()
    return produced


@pytest.mark.parametrize("stage", [Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS, Stage.ASSEMBLE])
def test_rerun_resumes_each_stage_through_assemble_to_the_goldens(tmp_path: Path, stage: Stage):
    root = tmp_path / "mod.forge"
    run_pipeline(root)
    events: list[StageEvent] = []
    provider = None if stage is Stage.ASSEMBLE else FixtureProvider(MINIMOD / "fixtures")
    result = rerun(root, stage, provider=provider, on_progress=events.append)
    assert result.report.validation.passed
    resumed = RUNNABLE_STAGES[RUNNABLE_STAGES.index(stage) :]
    assert [(event.stage, event.status) for event in events] == [
        (member, status) for member in resumed for status in ("running", "completed")
    ]
    assert produced_artifacts(root) == golden_files()


def test_rerun_assemble_needs_no_provider_and_is_the_correction_loop_assemble(tmp_path: Path):
    root = tmp_path / "mod.forge"
    run_pipeline(root)
    result = rerun(root, Stage.ASSEMBLE)
    assert result.adventure.name == "The Root Cellar of Old Wenna"


def test_rerun_preprocess_skips_the_self_copy_and_writes_a_fresh_pending_run(tmp_path: Path):
    root = tmp_path / "mod.forge"
    preprocess(MINIMOD / "minimod.pdf", root, ConversionSettings())
    workdir = Workdir(root)
    events: list[StageEvent] = []
    # The stub fails the survey — the assertion is about the preprocess leg:
    # no SameFileError on the workdir's own source.pdf, a fresh all-pending
    # RunMeta, and the chain continuing into survey.
    with pytest.raises(ProviderError):
        rerun(root, Stage.PREPROCESS, provider=ScriptedProvider([ProviderError("boom")]), on_progress=events.append)
    run = workdir.read_run()
    assert run.stages[Stage.PREPROCESS].status == "completed"
    assert run.stages[Stage.SURVEY].status == "failed"
    assert run.stages[Stage.CONTENT].status == "pending"
    assert run.stages[Stage.ASSEMBLE].status == "pending"
    assert [(event.stage, event.status) for event in events] == [
        (Stage.PREPROCESS, "running"),
        (Stage.PREPROCESS, "completed"),
        (Stage.SURVEY, "running"),
        (Stage.SURVEY, "failed"),
    ]


def test_rerun_preprocess_applies_blank_page_renders(tmp_path: Path):
    root = tmp_path / "mod.forge"
    preprocess(MINIMOD / "minimod.pdf", root, ConversionSettings())
    workdir = Workdir(root)
    rendered = Image.open(workdir.page_png(1)).convert("RGB")
    assert rendered.getcolors(rendered.width * rendered.height) != [(rendered.width * rendered.height, (255, 255, 255))]
    with pytest.raises(ProviderError):
        rerun(
            root,
            Stage.PREPROCESS,
            provider=ScriptedProvider([ProviderError("stop after preprocess")]),
            settings_updates={"blank_page_renders": [1]},
        )
    blanked = Image.open(workdir.page_png(1)).convert("RGB")
    assert blanked.size == rendered.size  # the page's normal pixel size
    assert blanked.getcolors() == [(blanked.width * blanked.height, (255, 255, 255))]
    assert workdir.page_txt(1).read_text(encoding="utf-8").strip()  # text layer still extracted
    assert workdir.read_run().settings.blank_page_renders == (1,)


def test_rerun_preprocess_rejects_out_of_range_blank_pages(tmp_path: Path):
    root = tmp_path / "mod.forge"
    preprocess(MINIMOD / "minimod.pdf", root, ConversionSettings())
    with pytest.raises(PdfError, match="blank_page_renders"):
        rerun(
            root,
            Stage.PREPROCESS,
            provider=ScriptedProvider([]),
            settings_updates={"blank_page_renders": [99]},
        )


def test_rerun_assemble_with_set_flips_the_unresolved_fallback(tmp_path: Path):
    # The phase 2 deferral, closed: flipping the knob on an existing workdir
    # goes through rerun assemble --set, never a re-extraction.
    workdir = synthetic_workdir(tmp_path / "mod.forge")
    result = rerun(workdir.root, Stage.ASSEMBLE, settings_updates={"unresolved_fallback": "omit"})
    guard_post = next(area for area in result.adventure.dungeons[0].levels[0].areas if area.id == "1")
    assert guard_post.encounter is None  # under best-effort this held a flagged stand-in
    report_area = next(area for area in result.report.areas if area.id == "barrow/1/1")
    assert "monster_unresolved:hobgoblin chieftain" in report_area.flags
    assert workdir.read_run().settings.unresolved_fallback == "omit"


def test_drift_guard_rejects_upstream_knobs_and_allows_downstream(tmp_path: Path):
    root = tmp_path / "mod.forge"
    run_pipeline(root)
    with pytest.raises(ValueError, match="rerun preprocess instead"):
        rerun(
            root, Stage.MONSTERS, provider=FixtureProvider(MINIMOD / "fixtures"), settings_updates={"render_dpi": 100}
        )
    with pytest.raises(ValueError, match="rerun monsters instead"):
        rerun(root, Stage.ASSEMBLE, settings_updates={"monster_llm_top_k": 4})
    # Same-stage and downstream knobs pass the guard.
    result = rerun(root, Stage.ASSEMBLE, settings_updates={"unresolved_fallback": "best-effort"})
    assert result.report.validation.passed


def test_unknown_knob_is_a_validation_error(tmp_path: Path):
    root = tmp_path / "mod.forge"
    run_pipeline(root)
    with pytest.raises(ValidationError):
        rerun(root, Stage.ASSEMBLE, settings_updates={"no_such_knob": 1})


def test_rerun_requires_a_provider_exactly_when_the_chain_has_model_stages(tmp_path: Path):
    root = tmp_path / "mod.forge"
    run_pipeline(root)
    with pytest.raises(ValueError, match="survey, content, monsters"):
        rerun(root, Stage.SURVEY)
    with pytest.raises(ValueError, match="monsters"):
        rerun(root, Stage.MONSTERS)


def test_rerun_rejects_the_geometry_stage(tmp_path: Path):
    root = tmp_path / "mod.forge"
    run_pipeline(root)
    with pytest.raises(ValueError, match="no independent run"):
        rerun(root, Stage.GEOMETRY)


def test_rerun_precondition_failures_are_the_stage_functions_own(tmp_path: Path):
    workdir = Workdir(tmp_path / "empty.forge")
    workdir.stages_dir.mkdir(parents=True)
    from datetime import UTC, datetime

    from osrforge.contracts.run import RunMeta, StageStatus

    stages = {stage: StageStatus() for stage in Stage}
    stages[Stage.PREPROCESS] = StageStatus(
        status="completed",
        started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
    )
    workdir.write_run(
        RunMeta(source_sha256="00" * 32, source_bytes=1, page_count=1, settings=ConversionSettings(), stages=stages)
    )
    with pytest.raises(ValueError, match="completed monsters stage"):
        rerun(workdir.root, Stage.ASSEMBLE)


def test_failure_mid_chain_then_rerun_completes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from importlib import import_module

    from test_convert import fabricated_preprocess

    convert_module = import_module("osrforge.convert")
    monkeypatch.setattr(convert_module, "preprocess", fabricated_preprocess)
    from osrforge.convert import convert

    root = tmp_path / "mod.forge"
    with pytest.raises(ProviderError):
        convert(MINIMOD / "minimod.pdf", root, FailAfter(FixtureProvider(MINIMOD / "fixtures"), passthrough=1))
    workdir = Workdir(root)
    assert workdir.read_run().stages[Stage.CONTENT].status == "failed"
    assert workdir.survey_json.is_file()  # upstream intact
    result = rerun(root, Stage.CONTENT, provider=FixtureProvider(MINIMOD / "fixtures"))
    assert result.report.validation.passed
    assert produced_artifacts(root) == golden_files()
