"""The playability lint and smoke delve: `findings = check(workdir)`.

`check` loads the adventure exactly as a consumer does (`check_document` +
`Adventure.model_validate`), so every run also exercises the artifact
contract, then runs its two tiers and merges the findings into
`report.json`. Everything is deterministic — the delve seed is a module
constant — so the purity guarantee extends: `assemble && check` twice is
byte-identical.

Tier 1 is `validate_adventure`, osrlib's own content gate; its outcome feeds
the CLI verdict, not `findings` — osrlib's errors are strings with an existing
report home (`validation`), and duplicating them as findings would make two
sources of truth. Tier 2 is the static graph checks plus the smoke delve.

Three graph flavors over each level's edge map, pinned once: *inclusive*
(open edges plus every door, any state — what a party can traverse in
principle), *non-secret* (secret-door edges become walls), and *deterministic*
(open edges plus plain doors only — no stuck, locked, or secret; what a
scripted walk can traverse without probabilistic commands). Transitions are
directed edges between cells. The smoke delve walks the deterministic
subgraph reactively and the static checks own the rest.
"""

import json
import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import (
    CommandResult,
    EnterDungeon,
    Evade,
    MoveParty,
    OpenDoor,
    SessionMode,
    UseStairs,
    Wait,
)
from osrlib.crawl.dungeon import Direction, DungeonSpec, Edge, EdgeKind, LevelSpec, Position, edge_key, step
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.versioning import check_document

from osrforge.contracts.report import ExtractionReport, LintCheck, LintFinding
from osrforge.workdir import Workdir, write_json_artifact

__all__ = ["CHECK_SEED", "SEVERITY", "check"]

CHECK_SEED = 20260709
"""The pinned delve seed: the party roll-up and every in-play die derive from it."""

SEVERITY: Mapping[LintCheck, Literal["error", "warning"]] = {
    LintCheck.EDGE_INVALID: "error",
    LintCheck.AREA_UNREACHABLE: "error",
    LintCheck.ORPHAN_CELL: "warning",
    LintCheck.SECRET_ONLY_ACCESS: "warning",
    LintCheck.TRANSITION_UNPAIRED: "warning",
    LintCheck.DELVE_BLOCKED: "error",
    LintCheck.DELVE_INCOMPLETE: "warning",
}
"""Each check's severity — the producer's pin, hoisted to one importable table.

Severity is a field on [`LintFinding`][osrforge.contracts.report.LintFinding]
rather than a function of the id (the contract needn't change if a check's
severity is ever re-judged), so this table is where the producer's judgment
lives: every emission site and the generated vocabulary page read it here.
"""

_ENCOUNTER_BUDGET = 20
"""Commands allowed to disengage one encounter before the delve gives up."""

_COMMAND_CAP = 200
"""The hard per-dungeon command cap — a runaway guard, not a tuning knob."""

_EDGE_KEY_SHAPE = re.compile(r"^(-?[0-9]+),(-?[0-9]+):(north|south|east|west)$")


def _passable(edge: Edge, *, include_secret: bool) -> bool:
    """The inclusive/non-secret flavors: open edges plus doors, optionally minus secret ones."""
    if edge.kind is EdgeKind.OPEN:
        return True
    if edge.kind is EdgeKind.DOOR:
        return include_secret or (edge.door is not None and edge.door.kind != "secret")
    return False


def _deterministically_passable(edge: Edge) -> bool:
    """The deterministic flavor: open edges plus plain doors only."""
    if edge.kind is EdgeKind.OPEN:
        return True
    if edge.kind is EdgeKind.DOOR and edge.door is not None:
        door = edge.door
        return door.kind == "normal" and not door.stuck and not door.locked
    return False


_Node = tuple[str, int, Position]


def _reachable(adventure: Adventure, *, include_secret: bool) -> set[_Node]:
    """BFS over one flavor's graph plus directed transitions, seeded from every dungeon's entrance."""
    levels = {(dungeon.id, level.number): level for dungeon in adventure.dungeons for level in dungeon.levels}
    seeds: list[_Node] = []
    for dungeon in adventure.dungeons:
        # Only the first entrance-bearing level is a play seed — osrlib's
        # EnterDungeon uses exactly this expression, and an override-authored
        # second entrance must not manufacture phantom reachability.
        entrance_level = next((level for level in dungeon.levels if level.entrance is not None), None)
        if (
            entrance_level is not None
            and entrance_level.entrance is not None
            and entrance_level.in_bounds(entrance_level.entrance)
        ):
            seeds.append((dungeon.id, entrance_level.number, entrance_level.entrance))
    visited: set[_Node] = set(seeds)
    queue = deque(seeds)
    while queue:
        dungeon_id, number, cell = queue.popleft()
        level = levels[(dungeon_id, number)]
        for direction in Direction:
            if not _passable(level.edge(cell, direction), include_secret=include_secret):
                continue
            node = (dungeon_id, number, step(cell, direction))
            if node not in visited:
                visited.add(node)
                queue.append(node)
        for transition in level.transitions:
            if transition.position != cell:
                continue
            target = levels.get((transition.to_dungeon_id, transition.to_level_number))
            if target is None or not target.in_bounds(transition.to_position):
                continue
            node = (transition.to_dungeon_id, transition.to_level_number, transition.to_position)
            if node not in visited:
                visited.add(node)
                queue.append(node)
    return visited


def _edge_invalid_findings(adventure: Adventure) -> list[LintFinding]:
    """Keys osrlib would silently ignore — the single most dangerous silent failure in the loop."""
    findings: list[LintFinding] = []

    def finding(location: str, message: str) -> None:
        findings.append(
            LintFinding(
                id=LintCheck.EDGE_INVALID,
                severity=SEVERITY[LintCheck.EDGE_INVALID],
                location=location,
                message=message,
            )
        )

    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            location = f"{dungeon.id}/{level.number}"
            for key in level.edges:
                match = _EDGE_KEY_SHAPE.match(key)
                if match is None:
                    finding(location, f"edge key {key!r} is malformed — expected 'x,y:side'")
                    continue
                x, y = int(match.group(1)), int(match.group(2))
                canonical = edge_key((x, y), Direction(match.group(3)))
                if key != canonical:
                    finding(
                        location,
                        f"edge key {key!r} is never consulted — osrlib's canonical form is {canonical!r}",
                    )
                    continue
                incident = ((x, y), step((x, y), Direction(match.group(3))))
                for cell in incident:
                    if not level.in_bounds(cell):
                        finding(location, f"edge key {key!r} references the out-of-bounds cell {cell}")
                        break
    return findings


def _static_findings(adventure: Adventure) -> list[LintFinding]:
    """Tier 2, static: the graph checks, grouped by check id in the vocabulary's order."""
    findings = _edge_invalid_findings(adventure)
    inclusive = _reachable(adventure, include_secret=True)
    non_secret = _reachable(adventure, include_secret=False)

    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            for area in level.areas:
                if not any((dungeon.id, level.number, cell) in inclusive for cell in area.cells):
                    findings.append(
                        LintFinding(
                            id=LintCheck.AREA_UNREACHABLE,
                            severity=SEVERITY[LintCheck.AREA_UNREACHABLE],
                            location=f"{dungeon.id}/{level.number}/{area.id}",
                            message="no path from any entrance reaches this area",
                        )
                    )

    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            area_cells = {cell for area in level.areas for cell in area.cells}
            for y in range(level.height):
                for x in range(level.width):
                    cell = (x, y)
                    if cell in area_cells or (dungeon.id, level.number, cell) in inclusive:
                        continue
                    # The preview renderer's own corridor definition: a non-area
                    # cell with at least one non-wall edge. Flagging every blank
                    # bounding-box cell would drown the report.
                    if any(level.edge(cell, direction).kind is not EdgeKind.WALL for direction in Direction):
                        findings.append(
                            LintFinding(
                                id=LintCheck.ORPHAN_CELL,
                                severity=SEVERITY[LintCheck.ORPHAN_CELL],
                                location=f"{dungeon.id}/{level.number}",
                                message=f"cell {cell} renders as corridor but no path reaches it",
                            )
                        )

    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            for area in level.areas:
                reached = any((dungeon.id, level.number, cell) in inclusive for cell in area.cells)
                reached_openly = any((dungeon.id, level.number, cell) in non_secret for cell in area.cells)
                if reached and not reached_openly:
                    findings.append(
                        LintFinding(
                            id=LintCheck.SECRET_ONLY_ACCESS,
                            severity=SEVERITY[LintCheck.SECRET_ONLY_ACCESS],
                            location=f"{dungeon.id}/{level.number}/{area.id}",
                            message="every path into this area passes through a secret door",
                        )
                    )

    levels = {(dungeon.id, level.number): level for dungeon in adventure.dungeons for level in dungeon.levels}
    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            for transition in level.transitions:
                if transition.kind not in ("stairs_up", "stairs_down"):
                    continue  # trapdoors and chutes are one-way by osrlib's design
                target = levels.get((transition.to_dungeon_id, transition.to_level_number))
                reciprocal = target is not None and any(
                    other.position == transition.to_position
                    and other.to_dungeon_id == dungeon.id
                    and other.to_level_number == level.number
                    and other.to_position == transition.position
                    for other in target.transitions
                )
                if not reciprocal:
                    findings.append(
                        LintFinding(
                            id=LintCheck.TRANSITION_UNPAIRED,
                            severity=SEVERITY[LintCheck.TRANSITION_UNPAIRED],
                            location=f"{dungeon.id}/{level.number}",
                            message=(
                                f"{transition.kind} at {transition.position} has no transition back from "
                                f"{transition.to_dungeon_id}/{transition.to_level_number} {transition.to_position}"
                            ),
                        )
                    )
    return findings


def _delve_party() -> Party:
    """The throwaway seeded party: four level-1 characters, rolled from the pinned seed."""
    rules = Ruleset()
    stream = RngStreams(master_seed=CHECK_SEED).get(CHARACTER_CREATION_STREAM)
    members = [
        create_character(
            name=name, class_id=class_id, alignment=Alignment.LAWFUL, ruleset=rules, stream=stream
        ).character
        for name, class_id in (("Hild", "fighter"), ("Rurik", "dwarf"), ("Mira", "cleric"), ("Fenn", "thief"))
    ]
    return Party(members=members)


@dataclass
class _DelveState:
    """One dungeon's session plus the hard command cap."""

    session: GameSession

    commands: int = 0

    def execute(self, command: object) -> CommandResult:
        self.commands += 1
        return self.session.execute(command)  # pyright: ignore[reportArgumentType]

    @property
    def capped(self) -> bool:
        return self.commands >= _COMMAND_CAP


def _disengage(state: _DelveState) -> str:
    """Resolve an open encounter: `Evade`, then `Wait` out any pursuit rounds.

    In osrlib a pursued evasion leaves the session in encounter mode and
    further `Evade` commands reject `already_evading`; `Wait` is what advances
    pursuit. Returns `"exploring"`, `"battle"` (battle pre-empted — pursuit can
    end at the party's heels), `"budget"` (the per-encounter budget ran out),
    or `"cap"` (the per-dungeon command cap ended the attempt).
    """
    spent = 0
    evaded = False
    while spent < _ENCOUNTER_BUDGET and not state.capped:
        mode = state.session.mode
        if mode is SessionMode.EXPLORING:
            return "exploring"
        if mode is not SessionMode.ENCOUNTER:
            return "battle"
        state.execute(Evade() if not evaded else Wait())
        evaded = True
        spent += 1
    if state.session.mode is SessionMode.EXPLORING:
        return "exploring"
    return "cap" if state.capped else "budget"


def _party_position(session: GameSession) -> Position | None:
    return session.dungeon_state.location.position


def _step_direction(from_cell: Position, to_cell: Position) -> Direction:
    for direction in Direction:
        if step(from_cell, direction) == to_cell:
            return direction
    raise AssertionError(f"cells {from_cell} and {to_cell} are not orthogonally adjacent")


def _deterministic_bfs(
    level: LevelSpec, start: Position
) -> tuple[dict[Position, Position | None], dict[Position, int]]:
    """Parents and distances over the deterministic subgraph, in pinned FIFO order."""
    parents: dict[Position, Position | None] = {start: None}
    distances = {start: 0}
    queue = deque([start])
    while queue:
        cell = queue.popleft()
        for direction in Direction:
            if not _deterministically_passable(level.edge(cell, direction)):
                continue
            neighbor = step(cell, direction)
            if neighbor not in parents:
                parents[neighbor] = cell
                distances[neighbor] = distances[cell] + 1
                queue.append(neighbor)
    return parents, distances


def _path_to(parents: dict[Position, Position | None], target: Position) -> list[Position]:
    path = [target]
    while (previous := parents[path[-1]]) is not None:
        path.append(previous)
    path.reverse()
    return path


def _walk(
    state: _DelveState,
    level: LevelSpec,
    path: list[Position],
    finding: Callable[[LintCheck, str], LintFinding],
) -> LintFinding | None:
    """Walk a computed path reactively; `None` means the walk completed clean."""
    expected = path[0]
    for target in path[1:]:
        if state.capped:
            return finding(LintCheck.DELVE_INCOMPLETE, "the delve hit the per-dungeon command cap")
        position = _party_position(state.session)
        if position != expected:
            return finding(
                LintCheck.DELVE_INCOMPLETE,
                f"the party was relocated to {position} mid-walk (a trap or transition fired)",
            )
        direction = _step_direction(expected, target)
        result = state.execute(MoveParty(direction=direction))
        if not result.accepted:
            code = result.rejections[0].code
            if code == "exploration.move.blocked" and level.edge(expected, direction).kind is EdgeKind.DOOR:
                # The reactive pattern: open and retry once — sidesteps
                # `starts_open` bookkeeping and swing-shut entirely.
                opened = state.execute(OpenDoor(direction=direction))
                if opened.accepted:
                    result = state.execute(MoveParty(direction=direction))
            if not result.accepted:
                return finding(
                    LintCheck.DELVE_BLOCKED,
                    f"a step the deterministic graph deems passable was rejected at {expected} "
                    f"moving {direction.value}: {result.rejections[0].code}",
                )
        outcome = _disengage(state)
        if outcome == "battle":
            return finding(LintCheck.DELVE_INCOMPLETE, "battle opened and pre-empted the walk")
        if outcome == "cap":
            return finding(LintCheck.DELVE_INCOMPLETE, "the delve hit the per-dungeon command cap")
        if outcome == "budget":
            return finding(LintCheck.DELVE_INCOMPLETE, "an encounter exhausted its disengage budget")
        expected = target
    return None


def _delve_dungeon(adventure: Adventure, dungeon: DungeonSpec) -> list[LintFinding]:
    """The smoke delve: entrance level, deterministic-subgraph path, encounters evaded."""

    def finding(check_id: LintCheck, message: str) -> LintFinding:
        return LintFinding(id=check_id, severity=SEVERITY[check_id], location=dungeon.id, message=message)

    entrance_level = next((level for level in dungeon.levels if level.entrance is not None), None)
    if entrance_level is None or entrance_level.entrance is None:
        return []  # unreachable when tier 1 passed; defensive against future callers
    state = _DelveState(session=GameSession.new(_delve_party(), adventure, seed=CHECK_SEED))
    entered = state.execute(EnterDungeon(dungeon_id=dungeon.id))
    if not entered.accepted:
        return [finding(LintCheck.DELVE_BLOCKED, f"EnterDungeon rejected: {entered.rejections[0].code}")]
    outcome = _disengage(state)
    if outcome != "exploring":
        messages = {
            "battle": "battle opened at the entrance",
            "budget": "the entrance encounter could not be evaded",
            "cap": "the delve hit the per-dungeon command cap",
        }
        return [finding(LintCheck.DELVE_INCOMPLETE, messages[outcome])]

    parents, distances = _deterministic_bfs(entrance_level, entrance_level.entrance)
    best_distance = max(distances.values())
    farthest = min(
        (cell for cell, distance in distances.items() if distance == best_distance),
        key=lambda cell: (cell[1], cell[0]),
    )
    walk_finding = _walk(state, entrance_level, _path_to(parents, farthest), finding)
    if walk_finding is not None:
        return [walk_finding]

    reachable_transition = next(
        (transition for transition in entrance_level.transitions if transition.position in distances), None
    )
    if reachable_transition is None or state.capped:
        return []
    position = _party_position(state.session)
    if position is None or position not in distances:
        return []
    stair_parents, _ = _deterministic_bfs(entrance_level, position)
    walk_finding = _walk(state, entrance_level, _path_to(stair_parents, reachable_transition.position), finding)
    if walk_finding is not None:
        return [walk_finding]
    used = state.execute(UseStairs())
    if not used.accepted:
        return [
            finding(
                LintCheck.DELVE_BLOCKED,
                f"the spec authors a transition at {reachable_transition.position} "
                f"but UseStairs was rejected: {used.rejections[0].code}",
            )
        ]
    outcome = _disengage(state)
    if outcome != "exploring":
        return [finding(LintCheck.DELVE_INCOMPLETE, "the transition's arrival encounter pre-empted the delve")]
    return []


def check(workdir_path: Path) -> tuple[LintFinding, ...]:
    """Run the two-tier playability check and merge the findings into `report.json`.

    Args:
        workdir_path: The workdir root; `adventure.json` and `report.json`
            must exist (an assembled draft is the input).

    Returns:
        The findings, exactly as merged into the rewritten report. A draft
        that fails tier 1 skips the smoke delve — its findings would be noise
        — but keeps the static checks.

    Raises:
        ValueError: If `adventure.json` or `report.json` is missing —
            `assemble` first.
    """
    workdir = Workdir(workdir_path)
    if not workdir.adventure_json.is_file():
        raise ValueError(f"adventure.json is missing: {workdir.adventure_json} — run assemble first")
    if not workdir.report_json.is_file():
        raise ValueError(f"report.json is missing: {workdir.report_json} — run assemble first")
    document = json.loads(workdir.adventure_json.read_text(encoding="utf-8"))
    adventure = Adventure.model_validate(check_document(document, "adventure"))
    report = ExtractionReport.model_validate_json(workdir.report_json.read_text(encoding="utf-8"))

    try:
        validate_adventure(adventure, load_monsters(), load_equipment())
        validation_passed = True
    except ContentValidationError:
        validation_passed = False

    findings = _static_findings(adventure)
    if validation_passed:
        for dungeon in adventure.dungeons:
            findings.extend(_delve_dungeon(adventure, dungeon))

    write_json_artifact(workdir.report_json, report.model_copy(update={"findings": tuple(findings)}))
    return tuple(findings)
