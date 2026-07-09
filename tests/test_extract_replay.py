"""Replay the chaotic-caves excerpt chain over the committed 8 pages — zero network.

The chain mirrors the runner's excerpt mode end to end: replay the excerpt
survey fixture, normalize with the real module's page count, filter every page
reference down to the committed subset (the pinned closure step), plan the
first content batch, build its request through the real builder, and replay
the content fixture. Page parts come exclusively from
tests/assets/chaotic-caves/pages/.

These fixtures live in fixtures-extract/replay/ — classification is by
directory, not tag: the milestone evidence in fixtures-extract/evidence/
shares tags (both surveys are tagged `survey`) and carries no replay promise.
"""

import json
from pathlib import Path
from typing import Any, cast

from osrforge.content import build_batch_request, plan_content_batches
from osrforge.pages import page_request_parts
from osrforge.providers.fixtures import FixtureProvider
from osrforge.settings import ConversionSettings
from osrforge.survey import build_survey_request, filter_index_to_pages, normalize_survey
from osrforge.workdir import Workdir

MODULE_DIR = Path(__file__).parent / "assets" / "chaotic-caves"
REPLAY_DIR = MODULE_DIR / "fixtures-extract" / "replay"
JN1_PAGE_COUNT = 48


def committed_pages(workdir: Workdir) -> list[int]:
    return sorted(int(path.stem) for path in workdir.pages_dir.glob("*.png"))


def test_excerpt_chain_replays_deterministically():
    # The committed subset keeps its original page numbering, so a Workdir
    # bound to the asset directory serves the pages directly.
    asset_workdir = Workdir(MODULE_DIR)
    pages = committed_pages(asset_workdir)
    assert pages == [8, 22, 23, 24, 25, 26, 27, 38]
    provider = FixtureProvider(REPLAY_DIR)

    survey_response = provider.generate(build_survey_request(page_request_parts(asset_workdir, pages)))
    index = normalize_survey(cast(dict[str, Any], survey_response.data), JN1_PAGE_COUNT)
    # The pinned lair-splitting behavior: JN1's five lairs are five dungeons,
    # and the town is not one of them.
    assert len(index.dungeons) == 5
    assert not any("town" in dungeon.id for dungeon in index.dungeons)

    index = filter_index_to_pages(index, pages)
    plans = plan_content_batches(index, ConversionSettings().content_batch_pages)
    batch = next(batch for plan in plans for batch in plan.batches)
    assert batch.tag == "content.orc-lair-a.1.b01"
    # Closure over the committed subset: the filtered index cannot reference an
    # uncommitted page, so the batch request is buildable at replay time.
    assert set(batch.part_pages) <= set(pages)

    content_response = provider.generate(
        build_batch_request(batch, page_request_parts(asset_workdir, batch.part_pages))
    )
    data = cast(dict[str, Any], content_response.data)
    keys = [entry["key"] for entry in data["areas"]]
    assert set(keys) <= {area.key for area in batch.areas}
    assert len(keys) == len(batch.areas)  # the recorded batch covered every key


def test_replay_directory_holds_exactly_the_chain_fixtures():
    tags = sorted(json.loads(path.read_text(encoding="utf-8"))["tag"] for path in REPLAY_DIR.glob("*.json"))
    assert tags == ["content.orc-lair-a.1.b01", "survey"]
