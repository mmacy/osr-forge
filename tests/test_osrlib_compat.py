"""The osrlib compatibility canary.

The phase 0 stand-in for the spec's golden-fixture compatibility gate — real
golden adventure.json fixtures arrive with assembly in phase 2. This still
fails loudly if an osrlib upgrade moves the stamped-document envelope or the
Adventure models underneath us.
"""

from osrlib.crawl.adventure import Adventure, TownSpec
from osrlib.crawl.dungeon import AreaSpec, DungeonSpec, LevelSpec
from osrlib.versioning import check_document, stamp_document


def make_minimal_adventure() -> Adventure:
    return Adventure(
        name="Canary",
        description="A minimal adventure for the osrlib compatibility canary.",
        town=TownSpec(name="Wennadale"),
        dungeons=(
            DungeonSpec(
                id="cellar",
                name="The Root Cellar",
                levels=(
                    LevelSpec(
                        number=1,
                        width=4,
                        height=4,
                        entrance=(0, 0),
                        areas=(AreaSpec(id="1", name="Collapsed stair", cells=((1, 1), (1, 2))),),
                    ),
                ),
            ),
        ),
    )


def test_stamp_check_model_validate_round_trip():
    adventure = make_minimal_adventure()
    document = stamp_document("adventure", adventure.model_dump(mode="json"))
    assert document["kind"] == "adventure"
    assert isinstance(document["schema_version"], int)
    assert isinstance(document["engine_version"], str)

    payload = check_document(document, "adventure")
    assert Adventure.model_validate(payload) == adventure
