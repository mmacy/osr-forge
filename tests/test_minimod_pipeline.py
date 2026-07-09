"""Replay the recorded minimod extraction through the real stage functions — zero network.

The workdir is fabricated from the committed page renders the fixtures were
recorded against (minimod's whole 5-page workdir is representable from
committed assets), so the stage functions build fingerprint-identical requests
and FixtureProvider answers them. The committed golden caches pin the
normalized stage output byte-for-byte.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from osrforge.content import content
from osrforge.contracts.report import AreaAddress
from osrforge.contracts.run import RunMeta, Stage, StageStatus
from osrforge.contracts.stages import CANONICAL_SLUG_PATTERN, LevelContent, SurveyIndex
from osrforge.providers.fixtures import FixtureProvider
from osrforge.settings import ConversionSettings
from osrforge.survey import survey
from osrforge.workdir import Workdir

MINIMOD = Path(__file__).parent / "assets" / "minimod"
PAGE_COUNT = 5


def minimod_workdir(root: Path) -> Workdir:
    workdir = Workdir(root)
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
        settings=ConversionSettings(),
        stages=stages,
    )
    workdir.write_run(run)
    return workdir


def run_pipeline(root: Path) -> dict[str, bytes]:
    workdir = minimod_workdir(root)
    provider = FixtureProvider(MINIMOD / "fixtures")
    survey(workdir, provider)
    content(workdir, provider)
    return {path.name: path.read_bytes() for path in sorted(workdir.stages_dir.iterdir())}


def test_pipeline_is_byte_stable_and_matches_the_goldens(tmp_path: Path):
    first = run_pipeline(tmp_path / "one.forge")
    second = run_pipeline(tmp_path / "two.forge")
    assert first == second
    goldens = {path.name: path.read_bytes() for path in sorted((MINIMOD / "expected").iterdir())}
    assert first == goldens


def test_run_json_records_both_stages_with_usage_and_identity(tmp_path: Path):
    workdir = minimod_workdir(tmp_path / "mod.forge")
    provider = FixtureProvider(MINIMOD / "fixtures")
    survey(workdir, provider)
    content(workdir, provider)
    run = workdir.read_run()
    for stage in (Stage.SURVEY, Stage.CONTENT):
        status = run.stages[stage]
        assert status.status == "completed"
        assert status.usage is not None
        assert status.usage.input_tokens > 0 and status.usage.output_tokens > 0
    assert run.provider == "FixtureProvider"
    assert run.model_id == "gpt-5.4-2026-03-05"


def test_every_cached_address_is_canonical(tmp_path: Path):
    workdir = minimod_workdir(tmp_path / "mod.forge")
    provider = FixtureProvider(MINIMOD / "fixtures")
    index = survey(workdir, provider)
    levels = content(workdir, provider)
    for dungeon in index.dungeons:
        assert CANONICAL_SLUG_PATTERN.match(dungeon.id)
        for level in dungeon.levels:
            for area in level.areas:
                address = f"{dungeon.id}/{level.number}/{area.key}"
                assert str(AreaAddress.parse(address)) == address
    for level_content in levels:
        assert isinstance(level_content, LevelContent)
        for area in level_content.areas:
            address = f"{level_content.dungeon_id}/{level_content.level_number}/{area.key}"
            assert str(AreaAddress.parse(address)) == address
    # The caches parse back through the contracts.
    SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
