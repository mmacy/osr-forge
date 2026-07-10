"""The milestone's CI floor: a converted golden loads and plays — zero network.

Deliberately the floor, not the spec's smoke delve (a computed multi-room path
with doors belongs to phase 3's playability lint): load the committed golden
`adventure.json` through osrlib's own gate, build a seeded party, open a
session, enter the first dungeon, and move through an open edge and back.
Keyed encounters met along the way are evaded — the entrance area of a real
module can hold monsters (minimod's does), and evading is itself play.
"""

import json
from pathlib import Path

import pytest
from osrlib.core.alignment import Alignment
from osrlib.core.character import CHARACTER_CREATION_STREAM, create_character
from osrlib.core.rng import RngStreams
from osrlib.core.ruleset import Ruleset
from osrlib.crawl.adventure import Adventure, validate_adventure
from osrlib.crawl.commands import EnterDungeon, Evade, MoveParty, SessionMode
from osrlib.crawl.dungeon import Direction, step
from osrlib.crawl.party import Party
from osrlib.crawl.session import GameSession
from osrlib.data import load_equipment, load_monsters
from osrlib.versioning import check_document

ASSETS = Path(__file__).parent / "assets"
SEED = 20260709

GOLDEN_ADVENTURES = sorted(ASSETS.glob("*/expected/adventure.json"))


def seeded_party() -> Party:
    rules = Ruleset()
    stream = RngStreams(master_seed=SEED).get(CHARACTER_CREATION_STREAM)
    members = [
        create_character(
            name=name, class_id=class_id, alignment=Alignment.LAWFUL, ruleset=rules, stream=stream
        ).character
        for name, class_id in (("Hild", "fighter"), ("Rurik", "dwarf"), ("Mira", "cleric"), ("Fenn", "thief"))
    ]
    return Party(members=members)


def evade_if_met(session: GameSession) -> None:
    if session.mode is SessionMode.ENCOUNTER:
        session.execute(Evade())


@pytest.mark.parametrize("golden", GOLDEN_ADVENTURES, ids=lambda path: path.parent.parent.name)
def test_converted_golden_loads_and_plays(golden: Path):
    document = json.loads(golden.read_text(encoding="utf-8"))
    adventure = Adventure.model_validate(check_document(document, "adventure"))
    validate_adventure(adventure, load_monsters(), load_equipment())

    session = GameSession.new(seeded_party(), adventure, seed=SEED)  # re-validates inside
    dungeon = adventure.dungeons[0]
    entered = session.execute(EnterDungeon(dungeon_id=dungeon.id))
    assert entered.accepted
    evade_if_met(session)
    assert session.mode is SessionMode.EXPLORING

    level = next(level for level in dungeon.levels if level.entrance is not None)
    entrance = level.entrance
    assert entrance is not None
    open_directions = [direction for direction in Direction if level.edge(entrance, direction).kind.value == "open"]
    assert open_directions, "the entrance cell has no open edge"
    # Prefer stepping into corridor (no keyed encounter); any open edge works.
    corridor_directions = [
        direction for direction in open_directions if level.area_at(step(entrance, direction)) is None
    ]
    move_direction = (corridor_directions or open_directions)[0]

    there = session.execute(MoveParty(direction=move_direction))
    assert there.accepted
    evade_if_met(session)
    back = session.execute(MoveParty(direction=move_direction.opposite))
    assert back.accepted
