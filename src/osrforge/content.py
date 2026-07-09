"""Stage 2: the content pass — per-level batched extraction of keyed areas.

Batches are sliding windows over each level's sorted source-page set,
overlapping by one page so an area whose text spans a batch boundary is fully
visible to its owning batch. Each area belongs to exactly one batch, enforced
through the batch schema's key enum — the model cannot invent, misspell, or
duplicate a key the code didn't ask for, so the cross-batch merge is pure
concatenation and a duplicate key across batches is a code bug, not a model
behavior. Every prompt rule is pinned against observed junk in the phase 0
spike fixtures; see the phase 1 plan before changing one.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from osrforge.contracts.run import Stage
from osrforge.contracts.stages import (
    DICE_PATTERN,
    DIRECTIONS,
    AreaConnection,
    AreaContent,
    AreaEncounter,
    Direction,
    LevelContent,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
)
from osrforge.pages import clamp_pages, page_request_parts
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, TextPart
from osrforge.workdir import StageTracker, Workdir, track_stage, write_json_artifact

__all__ = [
    "CONTENT_SYSTEM",
    "ContentBatch",
    "LevelPlan",
    "batch_schema",
    "build_batch_request",
    "content",
    "plan_content_batches",
]

CONTENT_SYSTEM = """\
You extract keyed dungeon areas from tabletop adventure module pages. The user message names the areas to \
extract, then interleaves each page's extracted text (each headed by a [page N] marker) with that page's \
image. Return one entry per named area, using exactly the canonical keys given.

Rules:
- "encounters" are the area's hostile or monstrous inhabitants with stat blocks. Ordinary townsfolk and \
named NPCs without a creature type are not encounters — they belong in the description. Use the creature \
type as printed ("goblin"), putting proper names ("Snagg") in the description. Wandering-monster tables are \
not keyed encounters.
- Encounter counts: put a plain number in "count_fixed" and dice notation like "2d4" in "count_dice"; \
anything the module says about count that fits neither (rates, conditions, ranges) goes in "count_note". \
Leave unused count fields null.
- "trap" describes the area's trap, null when it has none. "features" lists notable fixtures a referee \
should know about (altars, pools, levers, secret doors); "treasure" lists the area's treasure as printed.
- "connections": derive each connection's "direction" from the level map when the text is silent; use \
"unknown" only when neither the text nor the map says. For "to_key", use the connected area's canonical key \
when it appears in the area list; otherwise its printed label.
- "source_pages" refer to the [page N] markers in this request, never to page numbers printed on the pages.
- "confidence" is your self-assessment in [0, 1] of how faithfully you extracted that area.
"""


@dataclass(frozen=True)
class ContentBatch:
    """One planned content-extraction request.

    Attributes:
        dungeon_id: The canonical dungeon id.
        dungeon_name: The dungeon's printed name, for the prompt.
        level_number: The level's number.
        number: The 1-based batch number within the level.
        pages: The batch's own pages — one window over the level's sorted
            source-page set. Empty when the level's areas have no source pages
            and the batch carries just the map pages.
        map_pages: The level's map pages (all of them, ascending). They ride on
            every batch — the map is how the model answers `direction` when
            the prose is silent.
        areas: The survey areas this batch must extract.
    """

    dungeon_id: str
    dungeon_name: str
    level_number: int
    number: int
    pages: tuple[int, ...]
    map_pages: tuple[int, ...]
    areas: tuple[SurveyArea, ...]

    @property
    def part_pages(self) -> tuple[int, ...]:
        """The request's page order: batch pages, then map pages not already in the batch."""
        return self.pages + tuple(page for page in self.map_pages if page not in self.pages)

    @property
    def tag(self) -> str:
        """The request tag: `content.<dungeon-id>.<level>.b<NN>` (slugs contain no dots)."""
        return f"content.{self.dungeon_id}.{self.level_number}.b{self.number:02d}"


@dataclass(frozen=True)
class LevelPlan:
    """One level's planned batches — empty when the level needs no model call."""

    dungeon_id: str
    level_number: int
    batches: tuple[ContentBatch, ...]


def _page_windows(page_set: Sequence[int], batch_pages: int) -> list[tuple[int, ...]]:
    """Slide windows over the sorted page set; consecutive windows share exactly one page."""
    windows: list[tuple[int, ...]] = []
    start = 0
    while True:
        windows.append(tuple(page_set[start : start + batch_pages]))
        if start + batch_pages >= len(page_set):
            return windows
        start += batch_pages - 1


def _plan_level(dungeon: SurveyDungeon, level: SurveyLevel, batch_pages: int) -> LevelPlan:
    if not level.areas:
        return LevelPlan(dungeon.id, level.number, ())
    page_set = sorted({page for area in level.areas for page in area.source_pages})
    if not page_set and not level.map_pages:
        # No pages at all: no model call; the areas stay content-less for
        # phase 2's placeholder-plus-flag treatment.
        return LevelPlan(dungeon.id, level.number, ())
    windows = _page_windows(page_set, batch_pages) if page_set else [()]
    assigned: list[list[SurveyArea]] = [[] for _ in windows]
    for area in level.areas:
        if not area.source_pages:
            # Pageless areas ride the level's last batch and are named in its prompt.
            assigned[-1].append(area)
            continue
        first_page = area.source_pages[0]
        # The earliest batch whose page set contains the area's first source
        # page — the boundary page sits in two windows; earliest wins.
        for window_index, window in enumerate(windows):
            if first_page in window:
                assigned[window_index].append(area)
                break
    batches: list[ContentBatch] = []
    for window, areas in zip(windows, assigned, strict=True):
        # A window every area of which is owned elsewhere has nothing to
        # extract; it is dropped and the surviving batches numbered 1..n.
        if not areas:
            continue
        batches.append(
            ContentBatch(
                dungeon_id=dungeon.id,
                dungeon_name=dungeon.name,
                level_number=level.number,
                number=len(batches) + 1,
                pages=window,
                map_pages=level.map_pages,
                areas=tuple(areas),
            )
        )
    return LevelPlan(dungeon.id, level.number, tuple(batches))


def plan_content_batches(index: SurveyIndex, batch_pages: int) -> tuple[LevelPlan, ...]:
    """Plan every level's batches, per `(dungeon, level)` in survey order.

    Each level's page set is the union of its areas' `source_pages` (already
    clamped by survey normalization), deduplicated and sorted; batches are
    sliding windows over that sorted list with stride `batch_pages - 1`, so
    consecutive batches share exactly one page. Contiguity of the page numbers
    themselves is irrelevant — a sparse set windows exactly like a dense one.

    Args:
        index: The normalized survey index.
        batch_pages: The `content_batch_pages` setting — a batch's own pages
            are capped at this count (the level's map pages ride on top).

    Returns:
        One plan per level, in survey order.
    """
    return tuple(_plan_level(dungeon, level, batch_pages) for dungeon in index.dungeons for level in dungeon.levels)


def batch_schema(keys: Sequence[str]) -> dict[str, object]:
    """Build one batch's JSON Schema, with `key` constrained to exactly the given canonical keys.

    The enum means the model cannot invent, misspell, or answer a key the code
    didn't ask for; a level's area count is far below the proven 512-value
    enum budget. The `count_dice` pattern mirrors osrlib's dice grammar
    exactly, so a schema-valid dice string is an osrlib-parseable dice string.

    Args:
        keys: The canonical area keys this request must extract.

    Returns:
        The batch schema.
    """
    return {
        "type": "object",
        "properties": {
            "areas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "enum": list(keys)},
                        "description": {"type": "string"},
                        "encounters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "monster": {"type": "string"},
                                    "count_fixed": {"type": ["integer", "null"], "minimum": 1},
                                    "count_dice": {"type": ["string", "null"], "pattern": DICE_PATTERN},
                                    "count_note": {"type": ["string", "null"]},
                                },
                                "required": ["monster", "count_fixed", "count_dice", "count_note"],
                                "additionalProperties": False,
                            },
                        },
                        "trap": {"type": ["string", "null"]},
                        "treasure": {"type": "array", "items": {"type": "string"}},
                        "features": {"type": "array", "items": {"type": "string"}},
                        "connections": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "to_key": {"type": "string"},
                                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                                },
                                "required": ["to_key", "direction"],
                                "additionalProperties": False,
                            },
                        },
                        "source_pages": {"type": "array", "items": {"type": "integer"}},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "key",
                        "description",
                        "encounters",
                        "trap",
                        "treasure",
                        "features",
                        "connections",
                        "source_pages",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["areas"],
        "additionalProperties": False,
    }


def _area_line(area: SurveyArea) -> str:
    printed = area.source_label if area.source_label is not None else area.key
    pages = ", ".join(str(page) for page in area.source_pages) if area.source_pages else "unknown"
    return f'- {area.key} | printed key "{printed}" | {area.name} | pages {pages}'


def build_batch_request(
    batch: ContentBatch,
    parts: Sequence[TextPart | ImagePart],
    missing: Sequence[SurveyArea] | None = None,
) -> ModelRequest:
    """Build one batch's request — or its missing-key follow-up.

    Public and pure so the extraction runner and fixture tests build
    fingerprint-identical requests without duplicating prompt code. The
    follow-up (`missing` given) sends the same parts with the prompt naming
    just the missing areas and the schema's key enum containing exactly the
    missing keys — so it cannot re-answer keys the batch already covered.

    Args:
        batch: The planned batch.
        parts: The batch's page parts, built over
            [`part_pages`][osrforge.content.ContentBatch.part_pages].
        missing: The areas the batch response skipped, for the one follow-up.

    Returns:
        The request, tagged `content.<dungeon>.<level>.b<NN>` (`.retry` for
        the follow-up).
    """
    target = tuple(missing) if missing is not None else batch.areas
    tag = f"{batch.tag}.retry" if missing is not None else batch.tag
    lines = "\n".join(_area_line(area) for area in target)
    header = (
        f'Extract the keyed areas listed below from dungeon "{batch.dungeon_name}" (id: {batch.dungeon_id}), '
        f"level {batch.level_number}.\n"
        "Areas to extract (canonical key | printed key | name | expected pages):\n"
        f"{lines}\n"
    )
    if batch.map_pages:
        header += f"This level's map is on page {', '.join(str(page) for page in batch.map_pages)}.\n"
    return ModelRequest(
        tag=tag,
        system=CONTENT_SYSTEM,
        parts=(TextPart(text=header), *parts),
        schema=batch_schema([area.key for area in target]),
    )


def _response_areas(data: object, page_count: int) -> list[AreaContent]:
    """Parse one schema-valid batch response; the first occurrence of a repeated key wins."""
    areas: list[AreaContent] = []
    seen: set[str] = set()
    for entry in cast(list[dict[str, Any]], cast(dict[str, Any], data)["areas"]):
        key = cast(str, entry["key"])
        if key in seen:
            continue
        seen.add(key)
        areas.append(
            AreaContent(
                key=key,
                description=cast(str, entry["description"]),
                encounters=tuple(
                    AreaEncounter(
                        monster=cast(str, encounter["monster"]),
                        count_fixed=cast(int | None, encounter["count_fixed"]),
                        count_dice=cast(str | None, encounter["count_dice"]),
                        count_note=cast(str | None, encounter["count_note"]),
                    )
                    for encounter in cast(list[dict[str, Any]], entry["encounters"])
                ),
                trap=cast(str | None, entry["trap"]),
                treasure=tuple(cast(list[str], entry["treasure"])),
                features=tuple(cast(list[str], entry["features"])),
                connections=tuple(
                    AreaConnection(
                        to_key=cast(str, connection["to_key"]),
                        direction=cast(Direction, connection["direction"]),
                    )
                    for connection in cast(list[dict[str, Any]], entry["connections"])
                ),
                source_pages=clamp_pages(cast(list[int], entry["source_pages"]), page_count),
                confidence=cast(float, entry["confidence"]),
            )
        )
    return areas


def _extract_level(
    workdir: Workdir,
    provider: ModelProvider,
    plan: LevelPlan,
    page_count: int,
    tracker: StageTracker,
) -> LevelContent:
    areas: list[AreaContent] = []
    seen: set[str] = set()
    for batch in plan.batches:
        parts = page_request_parts(workdir, batch.part_pages)
        response = provider.generate(build_batch_request(batch, parts))
        tracker.add_usage(response.usage)
        tracker.set_model(type(provider).__name__, response.model_id)
        batch_areas = _response_areas(response.data, page_count)
        covered = {area.key for area in batch_areas}
        missing = tuple(area for area in batch.areas if area.key not in covered)
        if missing:
            retry = provider.generate(build_batch_request(batch, parts, missing=missing))
            tracker.add_usage(retry.usage)
            tracker.set_model(type(provider).__name__, retry.model_id)
            batch_areas.extend(_response_areas(retry.data, page_count))
            # Keys still missing after the follow-up are absent from the cache;
            # the survey index remains the authority on what exists, and
            # assembly (phase 2) emits placeholders flagged low_confidence.
        for area in batch_areas:
            if area.key in seen:
                raise AssertionError(
                    f"duplicate area key across batch responses: "
                    f"{plan.dungeon_id}/{plan.level_number}/{area.key} — a planner bug, not a model behavior"
                )
            seen.add(area.key)
            areas.append(area)
    return LevelContent(dungeon_id=plan.dungeon_id, level_number=plan.level_number, areas=tuple(areas))


def content(workdir: Workdir, provider: ModelProvider) -> tuple[LevelContent, ...]:
    """Run stage 2: extract every level's areas and write the per-level caches.

    Reads `stages/survey.json` rather than taking the index as a parameter —
    the cache is the contract, matching `rerun` semantics. Stale
    `stages/areas.*.json` are deleted first (unlike survey's clear-on-success,
    because the incremental per-level writes could otherwise leave a stale
    mixture), and each level's cache is written as the level completes, so a
    mid-stage failure keeps finished levels.

    Args:
        workdir: A workdir whose survey stage is `completed`.
        provider: The model provider.

    Returns:
        Every level's content, in survey order.

    Raises:
        ValueError: If the survey stage is not `completed` or its cache is
            missing (programmer misuse).
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
    """
    run = workdir.read_run()
    survey_status = run.stages.get(Stage.SURVEY)
    if survey_status is None or survey_status.status != "completed":
        raise ValueError("content requires a completed survey stage")
    if not workdir.survey_json.is_file():
        raise ValueError(f"the survey cache is missing: {workdir.survey_json}")
    index = SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
    results: list[LevelContent] = []
    with track_stage(workdir, Stage.CONTENT) as tracker:
        for stale in workdir.stage_caches():
            stale.unlink()
        for plan in plan_content_batches(index, run.settings.content_batch_pages):
            level = _extract_level(workdir, provider, plan, run.page_count, tracker)
            workdir.stages_dir.mkdir(parents=True, exist_ok=True)
            write_json_artifact(workdir.areas_json(level.dungeon_id, level.level_number), level)
            results.append(level)
    return tuple(results)
