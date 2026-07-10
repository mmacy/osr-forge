"""The end-to-end `convert()`: chaining, progress events, and failure semantics.

Preprocess is monkeypatched to fabricate the workdir from the committed minimod
page renders — the fixtures' request fingerprints hash the page bytes, and PNG
byte-stability across pdfium versions and platforms is explicitly not a
contract, so a fresh render could strand the fixtures on another OS.
"""

import shutil
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest

from osrforge.contracts.run import RunMeta, Stage, StageStatus
from osrforge.convert import StageEvent, convert
from osrforge.errors import ProviderError
from osrforge.providers.base import ModelRequest, ModelResponse
from osrforge.providers.fixtures import FixtureProvider
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

# The package façade re-exports the `convert` *function* as an attribute of
# `osrforge`, shadowing the module attribute — resolve the module itself.
convert_module = import_module("osrforge.convert")

MINIMOD = Path(__file__).parent / "assets" / "minimod"
PAGE_COUNT = 5


def fabricated_preprocess(pdf_path: Path, workdir_path: Path, settings: ConversionSettings) -> RunMeta:
    workdir = Workdir(workdir_path)
    workdir.pages_dir.mkdir(parents=True)
    for path in (MINIMOD / "pages").iterdir():
        shutil.copyfile(path, workdir.pages_dir / path.name)
    stages = {stage: StageStatus() for stage in Stage}
    stages[Stage.PREPROCESS] = StageStatus(
        status="completed",
        started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
    )
    run = RunMeta(
        source_sha256="00" * 32,
        source_bytes=1,
        page_count=PAGE_COUNT,
        settings=settings,
        stages=stages,
    )
    workdir.write_run(run)
    return run


class FailAfter:
    """Delegates to an inner provider for `passthrough` calls, then raises."""

    def __init__(self, inner: FixtureProvider, passthrough: int) -> None:
        self.inner = inner
        self.remaining = passthrough

    def generate(self, request: ModelRequest) -> ModelResponse:
        if self.remaining == 0:
            raise ProviderError("rate limited")
        self.remaining -= 1
        return self.inner.generate(request)


def test_full_chain_events_and_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(convert_module, "preprocess", fabricated_preprocess)
    events: list[StageEvent] = []
    result = convert(
        MINIMOD / "minimod.pdf",
        tmp_path / "mod.forge",
        FixtureProvider(MINIMOD / "fixtures"),
        on_progress=events.append,
    )
    assert result.adventure.name == "The Root Cellar of Old Wenna"
    assert result.report.validation.passed is True
    assert result.run.stages[Stage.ASSEMBLE].status == "completed"
    chain = [Stage.PREPROCESS, Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS, Stage.ASSEMBLE]
    assert [(event.stage, event.status) for event in events] == [
        (stage, status) for stage in chain for status in ("running", "completed")
    ]
    # Completed events carry that stage's usage from run.json; the survey made
    # a model call, the monsters stage did not.
    by_stage = {event.stage: event for event in events if event.status == "completed"}
    survey_usage = by_stage[Stage.SURVEY].usage
    assert survey_usage is not None and survey_usage.input_tokens > 0
    monsters_usage = by_stage[Stage.MONSTERS].usage
    assert monsters_usage is not None and monsters_usage.input_tokens == 0
    for stage in chain:
        assert next(event for event in events if event.stage is stage and event.status == "running").usage is None
    workdir = Workdir(tmp_path / "mod.forge")
    assert workdir.adventure_json.is_file()
    assert workdir.report_json.is_file()
    assert workdir.preview_svg("the-root-cellar-of-old-wenna", 1).is_file()


def test_stage_failure_emits_failed_and_keeps_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(convert_module, "preprocess", fabricated_preprocess)
    events: list[StageEvent] = []
    provider = FailAfter(FixtureProvider(MINIMOD / "fixtures"), passthrough=1)  # survey succeeds, content fails
    with pytest.raises(ProviderError):
        convert(
            MINIMOD / "minimod.pdf",
            tmp_path / "mod.forge",
            provider,
            on_progress=events.append,
        )
    assert (events[-1].stage, events[-1].status) == (Stage.CONTENT, "failed")
    workdir = Workdir(tmp_path / "mod.forge")
    run = workdir.read_run()
    assert run.stages[Stage.SURVEY].status == "completed"
    assert run.stages[Stage.CONTENT].status == "failed"
    assert run.stages[Stage.MONSTERS].status == "pending"
    assert workdir.survey_json.is_file()  # upstream intact — the resume story
    assert not workdir.adventure_json.exists()
