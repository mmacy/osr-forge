from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from osrforge.contracts.run import RunMeta, Stage, StageStatus, TokenUsage
from osrforge.settings import ConversionSettings


def make_run_meta() -> RunMeta:
    stages = {stage: StageStatus() for stage in Stage}
    stages[Stage.PREPROCESS] = StageStatus(
        status="completed",
        started_at=datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 8, 12, 0, 5, tzinfo=UTC),
        usage=TokenUsage(input_tokens=0, output_tokens=0),
    )
    return RunMeta(
        source_sha256="ab" * 32,
        source_bytes=1234,
        page_count=5,
        settings=ConversionSettings(),
        stages=stages,
    )


def test_run_meta_round_trips():
    run = make_run_meta()
    assert RunMeta.model_validate(run.model_dump(mode="json")) == run


def test_stage_wire_values_pin_the_spec_names():
    assert [stage.value for stage in Stage] == [
        "preprocess",
        "survey",
        "content",
        "monsters",
        "geometry",
        "assemble",
    ]


def test_token_usage_addition():
    total = TokenUsage(input_tokens=10, output_tokens=2) + TokenUsage(input_tokens=5, output_tokens=1)
    assert total == TokenUsage(input_tokens=15, output_tokens=3)


def test_token_usage_rejects_negative():
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=-1)


def test_stage_status_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        StageStatus(status="completed", started_at=datetime(2026, 7, 8, 12, 0, 0))


def test_stage_status_normalizes_to_utc():
    from datetime import timedelta, timezone

    offset = timezone(timedelta(hours=-7))
    status = StageStatus(status="completed", started_at=datetime(2026, 7, 8, 5, 0, 0, tzinfo=offset))
    assert status.started_at is not None
    assert status.started_at.tzinfo == UTC
    assert status.started_at.hour == 12


def test_run_meta_rejects_unknown_keys():
    data = make_run_meta().model_dump(mode="json")
    data["extra"] = 1
    with pytest.raises(ValidationError):
        RunMeta.model_validate(data)


def test_run_meta_rejects_unknown_stage():
    data = make_run_meta().model_dump(mode="json")
    data["stages"]["decorate"] = {"status": "pending"}
    with pytest.raises(ValidationError):
        RunMeta.model_validate(data)
