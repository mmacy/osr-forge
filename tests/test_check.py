"""The playability lint and smoke delve, against hand-built adventures and the committed goldens.

Each static check has a minimal triggering adventure; the delve tests use the
pinned seed. `delve_blocked` needs an engine/graph disagreement, which is
impossible by construction (both read the same `LevelSpec`), so that test
injects the mismatch by widening the deterministic flavor to locked doors.
"""

import json
from pathlib import Path

import pytest
from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import (
    AreaSpec,
    DoorSpec,
    DungeonSpec,
    Edge,
    EdgeKind,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    TransitionSpec,
)
from osrlib.versioning import stamp_document

import osrforge.check as check_module
from osrforge.check import check
from osrforge.contracts.report import (
    ExtractionReport,
    LintCheck,
    ModuleInfo,
    MonsterSummary,
    ValidationResult,
)
from osrforge.contracts.run import TokenUsage
from osrforge.workdir import Workdir, write_json_artifact

ASSETS = Path(__file__).parent / "assets"

OPEN = Edge(kind=EdgeKind.OPEN)
PLAIN_DOOR = Edge(kind=EdgeKind.DOOR, door=DoorSpec())
STUCK_DOOR = Edge(kind=EdgeKind.DOOR, door=DoorSpec(stuck=True))
LOCKED_DOOR = Edge(kind=EdgeKind.DOOR, door=DoorSpec(locked=True))
SECRET_DOOR = Edge(kind=EdgeKind.DOOR, door=DoorSpec(kind="secret"))
WALL = Edge(kind=EdgeKind.WALL)


def corridor_level(
    edges: dict[str, Edge],
    *,
    number: int = 1,
    entrance: tuple[int, int] | None = (0, 0),
    transitions: tuple[TransitionSpec, ...] = (),
    encounter: KeyedEncounter | None = None,
) -> LevelSpec:
    """Room 1 at (0,0), corridor cell (1,0), room 2 at (2,0) — edges as given."""
    return LevelSpec(
        number=number,
        width=3,
        height=1,
        edges=edges,
        areas=(
            AreaSpec(id="1", name="One", cells=((0, 0),), encounter=encounter),
            AreaSpec(id="2", name="Two", cells=((2, 0),)),
        ),
        transitions=transitions,
        entrance=entrance,
    )


def make_adventure(*levels: LevelSpec) -> Adventure:
    return Adventure(
        name="Lint Fixture",
        town=TownSpec(name="Town"),
        dungeons=(DungeonSpec(id="lair", name="The Lair", levels=levels),),
    )


def draft_workdir(root: Path, adventure: Adventure) -> Workdir:
    workdir = Workdir(root)
    workdir.root.mkdir(parents=True)
    write_json_artifact(workdir.adventure_json, stamp_document("adventure", adventure.model_dump(mode="json")))
    report = ExtractionReport(
        module=ModuleInfo(title="Lint Fixture", pages=1),
        validation=ValidationResult(passed=True),
        monsters=MonsterSummary(resolved=0),
        usage=TokenUsage(),
    )
    write_json_artifact(workdir.report_json, report)
    return workdir


def by_id(findings, check_id: LintCheck):
    return [finding for finding in findings if finding.id is check_id]


# --------------------------------------------------------------- plumbing


def test_missing_artifacts_raise(tmp_path: Path):
    with pytest.raises(ValueError, match=r"adventure\.json"):
        check(tmp_path)
    workdir = draft_workdir(
        tmp_path / "mod.forge", make_adventure(corridor_level({"1,0:west": OPEN, "2,0:west": OPEN}))
    )
    workdir.report_json.unlink()
    with pytest.raises(ValueError, match=r"report\.json"):
        check(workdir.root)


def test_clean_draft_is_silent_and_the_rewritten_report_is_byte_stable(tmp_path: Path):
    workdir = draft_workdir(
        tmp_path / "mod.forge", make_adventure(corridor_level({"1,0:west": OPEN, "2,0:west": OPEN}))
    )
    findings = check(workdir.root)
    assert findings == ()
    first = workdir.report_json.read_bytes()
    report = ExtractionReport.model_validate_json(first.decode("utf-8"))
    assert report.findings == ()
    assert check(workdir.root) == ()
    assert workdir.report_json.read_bytes() == first


def test_findings_merge_preserves_the_rest_of_the_report(tmp_path: Path):
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(corridor_level({"1,0:west": OPEN})))
    before = ExtractionReport.model_validate_json(workdir.report_json.read_text(encoding="utf-8"))
    findings = check(workdir.root)
    assert findings != ()
    after = ExtractionReport.model_validate_json(workdir.report_json.read_text(encoding="utf-8"))
    assert after.findings == findings
    assert after.model_copy(update={"findings": ()}) == before


# --------------------------------------------------------------- the graph flavors, pinned directly


@pytest.mark.parametrize(
    ("edge", "inclusive", "non_secret", "deterministic"),
    [
        (OPEN, True, True, True),
        (WALL, False, False, False),
        (PLAIN_DOOR, True, True, True),
        (STUCK_DOOR, True, True, False),
        (LOCKED_DOOR, True, True, False),
        (SECRET_DOOR, True, False, False),
    ],
)
def test_graph_flavors(edge: Edge, inclusive: bool, non_secret: bool, deterministic: bool):
    assert check_module._passable(edge, include_secret=True) is inclusive
    assert check_module._passable(edge, include_secret=False) is non_secret
    assert check_module._deterministically_passable(edge) is deterministic


# --------------------------------------------------------------- static checks


def test_edge_invalid_flags_keys_osrlib_would_ignore(tmp_path: Path):
    level = corridor_level(
        {
            "1,0:west": OPEN,
            "2,0:west": OPEN,
            "0,0:east": OPEN,  # non-canonical: the eastern neighbour's west edge
            "not-an-edge": OPEN,  # malformed
            "0,0:north": OPEN,  # canonical form, but the incident cell (0, -1) is out of bounds
        }
    )
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level))
    findings = by_id(check(workdir.root), LintCheck.EDGE_INVALID)
    assert len(findings) == 3
    assert all(finding.severity == "error" for finding in findings)
    assert all(finding.location == "lair/1" for finding in findings)
    messages = "\n".join(finding.message for finding in findings)
    assert "canonical form is '1,0:west'" in messages  # the hint for 0,0:east
    assert "malformed" in messages
    assert "out-of-bounds" in messages


def test_area_unreachable_and_orphan_cell(tmp_path: Path):
    # Room 1 is sealed off from the corridor; the corridor still opens into room 2.
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(corridor_level({"2,0:west": OPEN})))
    findings = check(workdir.root)
    unreachable = by_id(findings, LintCheck.AREA_UNREACHABLE)
    assert [finding.location for finding in unreachable] == ["lair/1/2"]
    assert unreachable[0].severity == "error"
    orphans = by_id(findings, LintCheck.ORPHAN_CELL)
    assert len(orphans) == 1
    assert orphans[0].severity == "warning"
    assert "(1, 0)" in orphans[0].message


def test_fully_sealed_entrance_is_the_degenerate_unreachable_case(tmp_path: Path):
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(corridor_level({})))
    findings = check(workdir.root)
    assert [finding.location for finding in by_id(findings, LintCheck.AREA_UNREACHABLE)] == ["lair/1/2"]
    # The bare corridor cell has only wall edges — not an orphan, just blank grid.
    assert by_id(findings, LintCheck.ORPHAN_CELL) == []


def test_secret_only_access_is_a_warning(tmp_path: Path):
    level = corridor_level({"1,0:west": OPEN, "2,0:west": SECRET_DOOR})
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level))
    findings = check(workdir.root)
    assert by_id(findings, LintCheck.AREA_UNREACHABLE) == []
    secret = by_id(findings, LintCheck.SECRET_ONLY_ACCESS)
    assert [finding.location for finding in secret] == ["lair/1/2"]
    assert secret[0].severity == "warning"


def stairs(kind: str, position, to_level: int, to_position) -> TransitionSpec:
    return TransitionSpec(
        kind=kind,  # pyright: ignore[reportArgumentType]
        position=position,
        to_dungeon_id="lair",
        to_level_number=to_level,
        to_position=to_position,
        to_facing="north",  # pyright: ignore[reportArgumentType]
    )


def test_transition_unpaired_flags_one_way_stairs(tmp_path: Path):
    level_one = corridor_level(
        {"1,0:west": OPEN, "2,0:west": OPEN},
        transitions=(stairs("stairs_down", (2, 0), 2, (0, 0)),),
    )
    level_two = corridor_level({"1,0:west": OPEN, "2,0:west": OPEN}, number=2, entrance=None)
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level_one, level_two))
    findings = by_id(check(workdir.root), LintCheck.TRANSITION_UNPAIRED)
    assert [finding.location for finding in findings] == ["lair/1"]
    assert findings[0].severity == "warning"
    assert "stairs_down at (2, 0)" in findings[0].message


def test_reciprocal_stairs_and_one_way_chutes_are_silent(tmp_path: Path):
    level_one = corridor_level(
        {"1,0:west": OPEN, "2,0:west": OPEN},
        transitions=(
            stairs("stairs_down", (2, 0), 2, (0, 0)),
            stairs("chute", (0, 0), 2, (2, 0)),  # one-way by osrlib's design — exempt
        ),
    )
    level_two = corridor_level(
        {"1,0:west": OPEN, "2,0:west": OPEN},
        number=2,
        entrance=None,
        transitions=(stairs("stairs_up", (0, 0), 1, (2, 0)),),
    )
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level_one, level_two))
    assert by_id(check(workdir.root), LintCheck.TRANSITION_UNPAIRED) == []


def test_validation_failed_draft_keeps_static_checks_and_skips_the_delve(tmp_path: Path):
    dangling = KeyedEncounter(monsters=(KeyedMonster(template_id="no-such-monster", count_fixed=1),))
    level = corridor_level({"1,0:west": OPEN, "2,0:west": OPEN}, encounter=dangling)
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level))
    # The delve would crash on GameSession.new over an invalid adventure; the
    # skip is what makes this return instead of raising.
    assert check(workdir.root) == ()


# --------------------------------------------------------------- the smoke delve


def test_delve_walks_through_plain_doors_cleanly(tmp_path: Path):
    level = corridor_level({"1,0:west": PLAIN_DOOR, "2,0:west": PLAIN_DOOR})
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level))
    assert check(workdir.root) == ()


def test_delve_uses_reachable_stairs_once(tmp_path: Path):
    level_one = corridor_level(
        {"1,0:west": OPEN, "2,0:west": OPEN},
        transitions=(stairs("stairs_down", (2, 0), 2, (0, 0)),),
    )
    level_two = corridor_level(
        {"1,0:west": OPEN, "2,0:west": OPEN},
        number=2,
        entrance=None,
        transitions=(stairs("stairs_up", (0, 0), 1, (2, 0)),),
    )
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level_one, level_two))
    assert check(workdir.root) == ()


def test_delve_blocked_when_the_engine_disagrees_with_the_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Impossible by construction (walk and graph read the same spec), so the
    # mismatch is injected: the graph is widened to treat locked doors as
    # passable, and the engine then rejects both OpenDoor and the move.
    monkeypatch.setattr(
        check_module,
        "_deterministically_passable",
        lambda edge: edge.kind is not EdgeKind.WALL and (edge.door is None or edge.door.kind == "normal"),
    )
    level = corridor_level({"1,0:west": LOCKED_DOOR, "2,0:west": OPEN})
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level))
    findings = check(workdir.root)
    blocked = by_id(findings, LintCheck.DELVE_BLOCKED)
    assert len(blocked) == 1
    assert blocked[0].severity == "error"
    assert blocked[0].location == "lair"
    assert "exploration.move.blocked" in blocked[0].message or "door" in blocked[0].message


def test_delve_incomplete_when_the_entrance_encounter_cannot_disengage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(check_module, "_ENCOUNTER_BUDGET", 0)
    encounter = KeyedEncounter(monsters=(KeyedMonster(template_id="goblin", count_fixed=2),))
    level = corridor_level({"1,0:west": OPEN, "2,0:west": OPEN}, encounter=encounter)
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(level))
    findings = check(workdir.root)
    incomplete = by_id(findings, LintCheck.DELVE_INCOMPLETE)
    assert len(incomplete) == 1
    assert incomplete[0].severity == "warning"
    assert incomplete[0].location == "lair"


# --------------------------------------------------------------- the committed goldens


def golden_check(tmp_path: Path, module: str) -> tuple:
    import shutil

    expected = ASSETS / module / "expected"
    root = tmp_path / f"{module}.forge"
    root.mkdir(parents=True)
    shutil.copyfile(expected / "adventure.json", root / "adventure.json")
    shutil.copyfile(expected / "report.json", root / "report.json")
    return check(root)


def test_minimod_golden_delves_with_no_errors(tmp_path: Path):
    findings = golden_check(tmp_path, "minimod")
    assert [finding for finding in findings if finding.severity == "error"] == []
    # The pinned warning: a wandering encounter's attacks stance pre-empts the
    # walk under the pinned seed — module difficulty, not a geometry defect.
    assert [(finding.id, finding.location) for finding in findings] == [
        (LintCheck.DELVE_INCOMPLETE, "the-root-cellar-of-old-wenna")
    ]


def test_jn1_golden_delves_with_no_errors(tmp_path: Path):
    findings = golden_check(tmp_path, "chaotic-caves")
    assert [finding for finding in findings if finding.severity == "error"] == []
    assert all(finding.id is LintCheck.DELVE_INCOMPLETE for finding in findings)
    assert len(findings) == 13


def test_check_is_deterministic_on_a_real_module(tmp_path: Path):
    import shutil

    expected = ASSETS / "minimod" / "expected"
    root = tmp_path / "minimod.forge"
    root.mkdir(parents=True)
    shutil.copyfile(expected / "adventure.json", root / "adventure.json")
    shutil.copyfile(expected / "report.json", root / "report.json")
    first_findings = check(root)
    first_bytes = (root / "report.json").read_bytes()
    assert check(root) == first_findings
    assert (root / "report.json").read_bytes() == first_bytes


def test_json_report_serialization_shape(tmp_path: Path):
    workdir = draft_workdir(tmp_path / "mod.forge", make_adventure(corridor_level({"1,0:west": OPEN})))
    check(workdir.root)
    data = json.loads(workdir.report_json.read_text(encoding="utf-8"))
    finding = data["findings"][0]
    assert set(finding) == {"id", "severity", "location", "message"}
