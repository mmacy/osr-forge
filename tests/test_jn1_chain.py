"""The JN1 back-half chain: monsters fixture replay over the committed caches, then assembly to goldens.

The monsters request is text-only (names plus catalog candidates — no page
images), so it replays with zero network from the committed stage caches, the
installed osrlib catalog, and the prompt code alone; no page assets are
fabricated. Both tests skip until the JN1 monsters recording session lands
`stages/monsters.json` (see `tools/extract/README.md`) — the skip is the
honest state while the recording is pending, never a silent pass.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from osrforge.assemble import assemble
from osrforge.contracts.run import RunMeta, Stage, StageStatus, TokenUsage
from osrforge.monsters import monsters
from osrforge.providers.fixtures import FixtureProvider
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

JN1 = Path(__file__).parent / "assets" / "chaotic-caves"
PAGE_COUNT = 48

recorded = pytest.mark.skipif(
    not (JN1 / "stages" / "monsters.json").is_file(),
    reason="the JN1 monsters recording session has not run yet (tools/extract/README.md)",
)


def jn1_workdir(root: Path, monsters_completed: bool) -> Workdir:
    workdir = Workdir(root)
    workdir.stages_dir.mkdir(parents=True)
    for path in sorted((JN1 / "stages").glob("*.json")):
        if path.name == "monsters.json" and not monsters_completed:
            continue
        shutil.copyfile(path, workdir.stages_dir / path.name)
    stages = {stage: StageStatus() for stage in Stage}
    done = [Stage.PREPROCESS, Stage.SURVEY, Stage.CONTENT]
    if monsters_completed:
        done.append(Stage.MONSTERS)
    for stage in done:
        stages[stage] = StageStatus(
            status="completed",
            started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 9, 12, 0, 5, tzinfo=UTC),
            usage=TokenUsage(),
        )
    run = RunMeta(
        source_sha256="37e6325ad0ebd52077aedb9f7f247511709d80bb8a25e4dd8c95da83d2730240",
        source_bytes=15_769_968,
        page_count=PAGE_COUNT,
        settings=ConversionSettings(),
        stages=stages,
    )
    workdir.write_run(run)
    return workdir


@recorded
def test_monsters_fixture_replays_byte_equal_to_the_committed_cache(tmp_path: Path):
    workdir = jn1_workdir(tmp_path / "jn1.forge", monsters_completed=False)
    provider = FixtureProvider(JN1 / "fixtures-extract" / "replay")
    monsters(workdir, provider)
    assert workdir.monsters_json.read_bytes() == (JN1 / "stages" / "monsters.json").read_bytes()


@recorded
def test_assembly_matches_the_jn1_goldens(tmp_path: Path):
    workdir = jn1_workdir(tmp_path / "jn1.forge", monsters_completed=True)
    assemble(workdir.root)
    expected = JN1 / "expected"
    assert workdir.adventure_json.read_bytes() == (expected / "adventure.json").read_bytes()
    assert workdir.report_json.read_bytes() == (expected / "report.json").read_bytes()
    produced = {path.name: path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
    goldens = {path.name: path.read_bytes() for path in sorted((expected / "previews").iterdir())}
    assert produced == goldens
