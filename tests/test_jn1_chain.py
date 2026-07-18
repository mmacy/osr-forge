"""The JN1 back-half chain and the phase 3 milestone gate — zero network.

The monsters *resolution* request is text-only (names plus catalog candidates
— no page images), so it replays with zero network from the committed stage
caches, the installed osrlib catalog, and the prompt code alone; no page
assets are fabricated. The stat-block pass does not share that property — its
requests embed page renders the asset directory doesn't commit — so the
replay test runs under `custom_monsters: off` and the committed
`stages/statblocks.json` is evidence-grade (recorded by the JN1 monsters
session, consumed deterministically by the goldens and the eval baseline).
The tests skip until the JN1 monsters recording session lands
`stages/monsters.json` (see `tools/extract/README.md`) — the skip is the
honest state while the recording is pending, never a silent pass.

The milestone gate is staged to match who writes what: `assemble` emits
`findings: ()` by design while the committed corrected report carries
`check`'s findings, so the two writers are gated separately — assemble twice
against the corrected adventure and previews (intermediate report
byte-stable), then check against the committed post-check report.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from osrforge.assemble import assemble
from osrforge.check import check
from osrforge.contracts.report import ExtractionReport
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


def jn1_workdir(root: Path, monsters_completed: bool, settings: ConversionSettings | None = None) -> Workdir:
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
        settings=settings if settings is not None else ConversionSettings(),
        stages=stages,
    )
    workdir.write_run(run)
    return workdir


@recorded
def test_monsters_fixture_replays_byte_equal_to_the_committed_cache(tmp_path: Path):
    # `custom_monsters: off` skips the stat-block pass: its requests embed page
    # renders the asset directory doesn't commit, so only the text-only
    # resolution exchange carries the replay promise. The committed
    # `statblocks.json` is evidence-grade and untouched here (the off run
    # writes its own echo into the fabricated workdir only).
    workdir = jn1_workdir(
        tmp_path / "jn1.forge", monsters_completed=False, settings=ConversionSettings(custom_monsters="off")
    )
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


@recorded
def test_the_correction_milestone_gate(tmp_path: Path):
    """The phase 3 milestone, machine-checked: a bad draft fixed to publishable entirely through overrides.yaml."""
    workdir = jn1_workdir(tmp_path / "jn1.forge", monsters_completed=True)
    shutil.copyfile(JN1 / "overrides.yaml", workdir.overrides_yaml)
    corrected = JN1 / "expected-corrected"

    def assert_draft_matches_the_corrected_goldens() -> None:
        assert workdir.adventure_json.read_bytes() == (corrected / "adventure.json").read_bytes()
        produced = {path.name: path.read_bytes() for path in sorted(workdir.previews_dir.iterdir())}
        goldens = {path.name: path.read_bytes() for path in sorted((corrected / "previews").iterdir())}
        assert produced == goldens

    assemble(workdir.root)
    assert_draft_matches_the_corrected_goldens()
    intermediate_report = workdir.report_json.read_bytes()
    assemble(workdir.root)
    assert_draft_matches_the_corrected_goldens()
    assert workdir.report_json.read_bytes() == intermediate_report

    findings = check(workdir.root)
    # The committed corrected report is the post-check report — the session's
    # accepted warnings are thereby byte-pinned.
    assert workdir.report_json.read_bytes() == (corrected / "report.json").read_bytes()
    assert [finding for finding in findings if finding.severity == "error"] == []
    report = ExtractionReport.model_validate_json(workdir.report_json.read_text(encoding="utf-8"))
    assert report.validation.passed
    assert report.monsters.unresolved == ()
