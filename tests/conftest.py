from datetime import UTC, datetime
from pathlib import Path

import pytest

from osrforge.contracts.run import RunMeta, Stage, StageStatus, TokenUsage
from osrforge.providers.base import ModelRequest, ModelResponse
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

ASSETS = Path(__file__).parent / "assets"


@pytest.fixture
def minimod_pdf() -> Path:
    return ASSETS / "minimod" / "minimod.pdf"


@pytest.fixture
def encrypted_pdf() -> Path:
    return ASSETS / "minimod" / "encrypted.pdf"


def fabricate_workdir(root: Path, page_count: int, settings: ConversionSettings | None = None) -> Workdir:
    """Build a workdir with fake page files and a preprocess-completed run.json.

    The page bytes are placeholders, not real PNGs — fine for stub providers,
    which never decode them. Pipeline-replay tests overwrite pages/ with the
    committed renders their fixtures were recorded against.
    """
    workdir = Workdir(root)
    workdir.pages_dir.mkdir(parents=True)
    for number in range(1, page_count + 1):
        workdir.page_png(number).write_bytes(f"png-{number}".encode())
        workdir.page_txt(number).write_text(f"text of page {number}\n", encoding="utf-8")
    stages = {stage: StageStatus() for stage in Stage}
    stages[Stage.PREPROCESS] = StageStatus(
        status="completed",
        started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
    )
    run = RunMeta(
        source_sha256="00" * 32,
        source_bytes=1,
        page_count=page_count,
        settings=settings if settings is not None else ConversionSettings(),
        stages=stages,
    )
    workdir.write_run(run)
    return workdir


class ScriptedProvider:
    """A stub provider that replays queued data payloads and records every request.

    A queued Exception instance is raised instead of returned. Payloads are
    not schema-validated — hazard-shaped tests rely on that.
    """

    def __init__(self, responses: list[object]) -> None:
        self.requests: list[ModelRequest] = []
        self._responses = list(responses)

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(f"ScriptedProvider exhausted; unexpected request {request.tag!r}")
        data = self._responses.pop(0)
        if isinstance(data, Exception):
            raise data
        return ModelResponse(
            data=data,
            usage=TokenUsage(input_tokens=100, output_tokens=10),
            model_id="stub-model-1",
        )
