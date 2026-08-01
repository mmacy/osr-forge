"""Stage 4: the map-reading pass — one request per surveyed level over its printed map pages.

The map becomes extraction input here and only here: each request carries a
level's keyed-area list (printed forms) plus its unblanked map pages, and the
model proposes same-level adjacency pairs, doors on those pairs, and the
map-labeled entrance — nothing more. **No content-stage output rides the
request**: the map read is an independent second opinion on the prose's edge
facts, and feeding it prose connections would let one instrument contaminate
the other. Proposals cache **as answered, raw** — endpoint resolution happens
at reconcile time ([`osrforge.reconcile`][osrforge.reconcile]) in
deterministic code, so a reconciliation-policy fix never strands a recorded
fixture.

The stage plans its requests off the survey cache alone (`SurveyLevel.map_pages`
has fed the content pass's compass hint since phase 6); content and monsters
never touch the cache, so only a survey re-run clears it. Downstream-most of
the model stages by design: `rerun mapread` re-rolls only itself plus
assembly, and a map failure preserves the expensive content and monsters
caches.
"""

from collections.abc import Sequence
from typing import Any, cast

from osrforge.contracts.run import Stage
from osrforge.contracts.stages import (
    MAP_DOORS,
    MapDoor,
    MapEdgeProposal,
    MapLevelReading,
    MapReading,
    SurveyIndex,
    SurveyLevel,
)
from osrforge.pages import page_request_parts
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, TextPart
from osrforge.workdir import Workdir, track_stage, write_json_artifact

__all__ = [
    "MAPREAD_SYSTEM",
    "build_mapread_request",
    "mapread",
    "mapread_schema",
    "mapread_tag",
    "parse_mapread_response",
]

MAPREAD_SYSTEM = """\
You read one dungeon level's printed map from a tabletop adventure module. The user message names the \
dungeon and level and lists the level's keyed areas in printed order, then interleaves each map page's \
extracted text (each headed by a [page N] marker) with that page's image — the map is in the images; read \
them.

Rules:
- Propose adjacency only between the provided printed keys. Two keyed areas are adjacent when the map joins \
them directly by a door, opening, or passage, or through unkeyed passage in consecutive order along it.
- Report a door only where the map marks one; use "secret_door" only where the map's legend marks the door \
secret. "none" means the map joins the pair with no door marked.
- "entrance" is the keyed area holding the way into the level from outside, only where the map labels it \
(an entrance arrow, a labeled stair from the surface, a marked cave mouth). Answer null when the map labels \
none.
- Propose nothing that cannot be read: keys the map does not show, adjacencies you cannot trace, or an \
entrance the map does not label are omissions, never guesses.
"""


def mapread_tag(dungeon_id: str, level_number: int) -> str:
    """Return one level's request tag: `mapread.<dungeon-id>.<level>` within the tag charset.

    The canonical slug alphabet guarantees the tag parses unambiguously — no
    dots in dungeon ids.

    Args:
        dungeon_id: The canonical dungeon id.
        level_number: The 1-based level number.

    Returns:
        The request tag.
    """
    return f"mapread.{dungeon_id}.{level_number}"


def mapread_schema() -> dict[str, object]:
    """Build the map-reading JSON Schema: flat adjacencies plus a nullable entrance.

    Flat and tolerate-and-flag (the `AreaConnection` posture): endpoints are
    free strings — the printed keys ride the request only through the prompt
    text, because enum-locking the endpoints would reject an answer the
    reconciler could still resolve by slug, and unresolvable answers drop at
    reconcile time with a flag, never a crash. Level-independent as a
    result: one schema serves every request.

    Returns:
        The request schema.
    """
    return {
        "type": "object",
        "properties": {
            "adjacencies": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string"},
                        "door": {"type": "string", "enum": list(MAP_DOORS)},
                    },
                    "required": ["a", "b", "door"],
                    "additionalProperties": False,
                },
            },
            "entrance": {"type": ["string", "null"]},
        },
        "required": ["adjacencies", "entrance"],
        "additionalProperties": False,
    }


def build_mapread_request(
    dungeon_id: str,
    dungeon_name: str,
    level: SurveyLevel,
    parts: Sequence[TextPart | ImagePart],
) -> ModelRequest:
    """Build one level's map-reading request.

    Public and pure, like [`build_survey_request`][osrforge.survey.build_survey_request]
    — the extraction runner's targeted leg must build fingerprint-identical
    requests without duplicating prompt code. The keyed-area list rides in
    survey order as printed forms (`source_label or key`, the census's
    printed-form recovery precedent), so the proposal universe and the
    yardstick agree on spellings.

    Args:
        dungeon_id: The canonical dungeon id.
        dungeon_name: The dungeon's printed name (the id when unnamed).
        level: The survey level whose map is being read.
        parts: The level's unblanked map pages' interleaved parts
            ([`page_request_parts`][osrforge.pages.page_request_parts]).

    Returns:
        The request, tagged `mapread.<dungeon-id>.<level>`.

    Raises:
        ValueError: If no parts are given — a level with no sendable pages
            never builds a request (programmer misuse; the stage records an
            unread reading instead).
    """
    if not parts:
        raise ValueError("the map-reading request needs at least one map page part")
    printed = [area.source_label or area.key for area in level.areas]
    listed = "\n".join(f"- {form}" for form in printed)
    header = (
        f'Read the level map for "{dungeon_name or dungeon_id}", level {level.number}. '
        f"The level's keyed areas, in printed order:\n{listed}\n"
    )
    return ModelRequest(
        tag=mapread_tag(dungeon_id, level.number),
        system=MAPREAD_SYSTEM,
        parts=(TextPart(text=header), *parts),
        schema=mapread_schema(),
    )


def parse_mapread_response(data: object) -> tuple[tuple[MapEdgeProposal, ...], str | None]:
    """Parse one schema-valid map-reading response into raw proposals plus the entrance answer.

    Public for the same reason the request builder is: the extraction
    runner's targeted leg must produce a cache byte-identical to the stage's
    without duplicating parsing code.

    Args:
        data: The response data, already validated against
            [`mapread_schema`][osrforge.mapread.mapread_schema].

    Returns:
        The proposals as answered, and the entrance key (or `None`).
    """
    answer = cast(dict[str, Any], data)
    proposals = tuple(
        MapEdgeProposal(
            a=cast(str, entry["a"]),
            b=cast(str, entry["b"]),
            door=cast(MapDoor, entry["door"]),
        )
        for entry in cast(list[dict[str, Any]], answer["adjacencies"])
    )
    return proposals, cast(str | None, answer["entrance"])


def mapread(workdir: Workdir, provider: ModelProvider) -> MapReading:
    """Run stage 4: read every surveyed level's map; write `stages/mapread.json`.

    Per level, in survey order: pages in `blank_page_renders` are excluded
    from the sent set (preprocess emitted them as blank white PNGs — sending
    one buys nothing, and a blank read as an empty map would dispute every
    prose edge); a level with no map pages at all records `unread:no_map_pages`
    with no request; a nonempty `map_pages` whose every page is blanked
    records `unread:pages_blanked` with no request; a level with no keyed
    areas records `unread:no_keyed_areas` with no request (there are no
    printed keys to propose between); a level with two or more keyed areas
    whose answer proposes zero adjacencies records `unread:empty_reading` —
    a degenerate read must not assert map silence against the whole level —
    keeping the answer raw but discarding its assertions via the status; a
    single-area level keeps a zero-pair reading and its entrance proposal.

    Under `map_reading: off` the stage completes writing the knob-echoed
    cache with zero levels — the prose-only pipeline, bit for bit. The cache
    is a single atomic artifact; no pre-clearing is needed.

    Args:
        workdir: A workdir whose monsters stage is `completed`.
        provider: The model provider.

    Returns:
        The reading, as written to the cache.

    Raises:
        ValueError: If the monsters stage is not `completed` or the survey
            cache is missing (programmer misuse).
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
    """
    run = workdir.read_run()
    monsters_status = run.stages.get(Stage.MONSTERS)
    if monsters_status is None or monsters_status.status != "completed":
        raise ValueError("mapread requires a completed monsters stage")
    if not workdir.survey_json.is_file():
        raise ValueError(f"the survey cache is missing: {workdir.survey_json}")
    index = SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
    blanked = set(run.settings.blank_page_renders)
    with track_stage(workdir, Stage.MAPREAD) as tracker:
        readings: list[MapLevelReading] = []
        if run.settings.map_reading == "read":
            for dungeon in index.dungeons:
                for level in dungeon.levels:
                    pages = tuple(page for page in level.map_pages if page not in blanked)
                    if not level.map_pages:
                        reason = "no_map_pages"
                    elif not pages:
                        reason = "pages_blanked"
                    elif not level.areas:
                        reason = "no_keyed_areas"
                    else:
                        reason = None
                    if reason is not None:
                        readings.append(
                            MapLevelReading(
                                dungeon_id=dungeon.id,
                                level_number=level.number,
                                map_pages=(),
                                status="unread",
                                unread_reason=reason,
                            )
                        )
                        continue
                    request = build_mapread_request(dungeon.id, dungeon.name, level, page_request_parts(workdir, pages))
                    response = provider.generate(request)
                    tracker.add_usage(response.usage)
                    tracker.set_model(type(provider).__name__, response.model_id)
                    proposals, entrance_key = parse_mapread_response(response.data)
                    if len(level.areas) >= 2 and not proposals:
                        # The degenerate-read guard: zero pairs over a level
                        # with pairs to propose must not assert map silence
                        # against every prose edge — the answer stays raw,
                        # the status discards its assertions (entrance too).
                        readings.append(
                            MapLevelReading(
                                dungeon_id=dungeon.id,
                                level_number=level.number,
                                map_pages=pages,
                                status="unread",
                                unread_reason="empty_reading",
                                entrance_key=entrance_key,
                            )
                        )
                        continue
                    readings.append(
                        MapLevelReading(
                            dungeon_id=dungeon.id,
                            level_number=level.number,
                            map_pages=pages,
                            status="read",
                            proposals=proposals,
                            entrance_key=entrance_key,
                        )
                    )
        cache = MapReading(map_reading=run.settings.map_reading, levels=tuple(readings))
        workdir.stages_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(workdir.mapread_json, cache)
    return cache
