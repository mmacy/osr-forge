"""Replay the recorded minimod extraction through the real stage functions — zero network.

The workdir is fabricated from the committed page renders the fixtures were
recorded against (minimod's whole 5-page workdir is representable from
committed assets), so the stage functions build fingerprint-identical requests
and FixtureProvider answers them. The committed goldens pin the full chain —
stage caches, `adventure.json`, `report.json`, and the preview —
byte-for-byte. The monsters stage makes no model call: minimod's whole name
population resolves in the exact tier, and no monsters fixture exists for
FixtureProvider to answer with, so a call would fail loudly.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from osrforge.assemble import assemble
from osrforge.content import content
from osrforge.contracts.report import AreaAddress
from osrforge.contracts.run import RunMeta, Stage, StageStatus
from osrforge.contracts.stages import CANONICAL_SLUG_PATTERN, LevelContent, SurveyIndex
from osrforge.monsters import monsters
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
    monsters(workdir, provider)
    assemble(root)
    produced = {f"stages/{path.name}": path.read_bytes() for path in sorted(workdir.stages_dir.iterdir())}
    produced |= {f"previews/{path.name}": path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    produced["adventure.json"] = workdir.adventure_json.read_bytes()
    produced["report.json"] = workdir.report_json.read_bytes()
    return produced


def golden_files() -> dict[str, bytes]:
    # expected/ mirrors the workdir: stage caches at the top level (survey,
    # areas, monsters), artifacts by name, previews under previews/.
    goldens: dict[str, bytes] = {}
    for path in sorted((MINIMOD / "expected").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(MINIMOD / "expected").as_posix()
        if relative in ("adventure.json", "report.json") or relative.startswith("previews/"):
            goldens[relative] = path.read_bytes()
        else:
            goldens[f"stages/{relative}"] = path.read_bytes()
    return goldens


def test_full_chain_is_byte_stable_and_matches_the_goldens(tmp_path: Path):
    first = run_pipeline(tmp_path / "one.forge")
    second = run_pipeline(tmp_path / "two.forge")
    assert first == second
    assert first == golden_files()


def test_run_json_records_every_stage_with_usage_and_identity(tmp_path: Path):
    workdir = minimod_workdir(tmp_path / "mod.forge")
    provider = FixtureProvider(MINIMOD / "fixtures")
    survey(workdir, provider)
    content(workdir, provider)
    monsters(workdir, provider)
    assemble(workdir.root)
    run = workdir.read_run()
    for stage in (Stage.SURVEY, Stage.CONTENT):
        status = run.stages[stage]
        assert status.status == "completed"
        assert status.usage is not None
        assert status.usage.input_tokens > 0 and status.usage.output_tokens > 0
    for stage in (Stage.MONSTERS, Stage.GEOMETRY, Stage.ASSEMBLE):
        status = run.stages[stage]
        assert status.status == "completed"
        # Deterministic work: usage recorded, and zero (monsters made no call).
        assert status.usage is not None
        assert status.usage.input_tokens == 0 and status.usage.output_tokens == 0
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
