from pathlib import Path

import pytest

from conftest import fabricate_workdir
from osrforge.contracts.run import Stage, StageStatus, TokenUsage
from osrforge.workdir import Workdir, track_stage


def test_stage_cache_paths():
    workdir = Workdir(Path("my-module.forge"))
    assert workdir.survey_json == Path("my-module.forge/stages/survey.json")
    assert workdir.areas_json("a-orc-lair", 2) == Path("my-module.forge/stages/areas.a-orc-lair.2.json")


def test_stage_caches_lists_only_area_caches_sorted(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    workdir.stages_dir.mkdir()
    workdir.survey_json.write_text("{}", encoding="utf-8")
    workdir.areas_json("b-lair", 1).write_text("{}", encoding="utf-8")
    workdir.areas_json("a-lair", 1).write_text("{}", encoding="utf-8")
    assert workdir.stage_caches() == [workdir.areas_json("a-lair", 1), workdir.areas_json("b-lair", 1)]


def test_track_stage_writes_running_on_enter(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    with track_stage(workdir, Stage.SURVEY):
        during = workdir.read_run().stages[Stage.SURVEY]
        assert during.status == "running"
        assert during.started_at is not None
        assert during.finished_at is None


def test_track_stage_completes_with_usage_and_identity(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    with track_stage(workdir, Stage.SURVEY) as tracker:
        tracker.add_usage(TokenUsage(input_tokens=100, output_tokens=10))
        tracker.add_usage(TokenUsage(input_tokens=50, output_tokens=5))
        tracker.set_model("FixtureProvider", "gpt-5.4-2026-03-05")
    run = workdir.read_run()
    status = run.stages[Stage.SURVEY]
    assert status.status == "completed"
    assert status.started_at is not None and status.finished_at is not None
    assert status.usage == TokenUsage(input_tokens=150, output_tokens=15)
    assert run.provider == "FixtureProvider"
    assert run.model_id == "gpt-5.4-2026-03-05"


def test_track_stage_without_responses_leaves_identity_unset(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    with track_stage(workdir, Stage.CONTENT):
        pass
    run = workdir.read_run()
    assert run.stages[Stage.CONTENT].status == "completed"
    assert run.provider is None and run.model_id is None


def test_track_stage_failure_records_error_and_reraises(tmp_path: Path):
    workdir = fabricate_workdir(tmp_path / "mod.forge", page_count=1)
    before_pages = sorted(p.name for p in workdir.pages_dir.iterdir())
    with pytest.raises(RuntimeError, match="boom"), track_stage(workdir, Stage.SURVEY) as tracker:
        tracker.add_usage(TokenUsage(input_tokens=7, output_tokens=3))
        raise RuntimeError("boom")
    run = workdir.read_run()
    status = run.stages[Stage.SURVEY]
    assert status.status == "failed"
    assert status.error == "boom"
    assert status.usage == TokenUsage(input_tokens=7, output_tokens=3)
    # Upstream artifacts untouched.
    assert run.stages[Stage.PREPROCESS].status == "completed"
    assert sorted(p.name for p in workdir.pages_dir.iterdir()) == before_pages


def test_with_stage_and_with_model_are_pure():
    from osrforge.contracts.run import RunMeta
    from osrforge.settings import ConversionSettings

    stages = {stage: StageStatus() for stage in Stage}
    run = RunMeta(source_sha256="00" * 32, source_bytes=1, page_count=1, settings=ConversionSettings(), stages=stages)
    updated = run.with_stage(Stage.SURVEY, StageStatus(status="running"))
    assert run.stages[Stage.SURVEY].status == "pending"
    assert updated.stages[Stage.SURVEY].status == "running"
    identified = run.with_model("FoundryProvider", "gpt-5.4-2026-03-05")
    assert run.provider is None and run.model_id is None
    assert identified.provider == "FoundryProvider"
    assert identified.model_id == "gpt-5.4-2026-03-05"
