"""Stage 1: the survey pass — one structured-output request over the whole module.

The survey identifies title, hooks, town info, dungeons, levels, keyed areas
with page locations, and every monster name — the index that plans the content
passes. Normalization to canonical ids and keys happens here, at the source:
`contracts/report.py` pins the address grammar, and the content stage's cache
filenames and per-batch key enums need canonical forms before content runs.

Every system-prompt rule is pinned against an observed failure in the phase 0
spike fixtures; see the phase 1 plan before changing one.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Any, cast

from osrforge.contracts.run import Stage
from osrforge.contracts.stages import (
    AREA_KINDS,
    AreaKind,
    SurveyArea,
    SurveyDungeon,
    SurveyIndex,
    SurveyLevel,
    TownInfo,
)
from osrforge.errors import ExtractionError
from osrforge.pages import clamp_pages, page_request_parts
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, TextPart
from osrforge.workdir import Workdir, track_stage, write_json_artifact

__all__ = [
    "SURVEY_SCHEMA",
    "SURVEY_SYSTEM",
    "build_survey_request",
    "canonical_slug",
    "filter_index_to_pages",
    "normalize_survey",
    "survey",
]

SURVEY_SYSTEM = """\
You survey tabletop adventure modules. The user message interleaves every page's extracted text (each headed \
by a [page N] marker) with that page's image. Fill the survey schema from those pages.

Rules:
- A dungeon is a keyed adventuring site: caves, ruins, lairs, and the like. The town or home base and its \
buildings are never dungeons — describe them only in "town". Leave "town.name" empty only when the module \
genuinely leaves the town unnamed.
- One dungeon per independently keyed site: maps connected internally by stairs or shafts are levels of one \
dungeon; separate lairs or sites with their own maps and entrances are separate dungeons, even when they are \
drawn together on one regional map or share a running area-number sequence (a module keying "A. Orc Lair", \
"B. Goblin Lair" describes separate dungeons).
- "hooks" are the rumors, jobs, and reasons the party goes on the adventure — usually found in the module's \
introduction or background.
- Each level's "map_pages" lists the pages showing that level's map; each area's "source_pages" lists the \
pages describing it. Both refer to the [page N] markers in this request, never to page numbers printed on \
the pages themselves.
- An area's "key" is the module's printed key for it (like "5" or "4a"); when an area has no printed key, \
use its name. "monster_names" collects every monster name that appears anywhere in the module.
"""

SURVEY_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "string"}},
        "town": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"],
            "additionalProperties": False,
        },
        "dungeons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "levels": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "number": {"type": "integer"},
                                "map_pages": {"type": "array", "items": {"type": "integer"}},
                                "areas": {"type": "array", "items": {"$ref": "#/$defs/area"}},
                            },
                            "required": ["number", "map_pages", "areas"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "levels"],
                "additionalProperties": False,
            },
        },
        "monster_names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "hooks", "town", "dungeons", "monster_names"],
    "additionalProperties": False,
    "$defs": {
        "area": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "source_pages": {"type": "array", "items": {"type": "integer"}},
                "kind": {"type": "string", "enum": list(AREA_KINDS)},
            },
            "required": ["key", "name", "source_pages", "kind"],
            "additionalProperties": False,
        }
    },
}
"""The survey JSON schema, refined from the spike's proven shape.

Two deliberate changes from the spike schema: no model-supplied dungeon `id`
(canonical ids are osr-forge's construct, derived by slugging `name`), and
`map_pages` added per level (feeds the content stage's direction extraction).
The schema is a fraction of the proven e512-d8 budget.
"""


def build_survey_request(parts: Sequence[TextPart | ImagePart]) -> ModelRequest:
    """Build the survey request over already-built page parts.

    Public and pure so the extraction runner and fixture tests build
    fingerprint-identical requests without duplicating prompt code.

    Args:
        parts: The interleaved page parts, from
            [`page_request_parts`][osrforge.pages.page_request_parts].

    Returns:
        The request, tagged `survey`.
    """
    return ModelRequest(tag="survey", system=SURVEY_SYSTEM, parts=tuple(parts), schema=SURVEY_SCHEMA)


def canonical_slug(text: str) -> str:
    """Slug free text into the canonical id/key alphabet.

    NFKD-decompose, drop non-ASCII, lowercase, collapse every run outside
    `[a-z0-9]` to a single `-`, strip leading/trailing `-`. The result matches
    [`CANONICAL_SLUG_PATTERN`][osrforge.contracts.stages.CANONICAL_SLUG_PATTERN]
    or is empty (callers apply fallbacks).

    Args:
        text: The text to slug, e.g. a printed dungeon name or area key.

    Returns:
        The canonical slug, possibly empty.
    """
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def _assign_unique(forms: Sequence[str]) -> tuple[str, ...]:
    """Make already-slugged forms unique, pinned as reserve-then-bump.

    First, every form is reserved — each item whose form is the *first*
    occurrence keeps it; then each later duplicate takes the lowest `-2`,
    `-3`, … suffix colliding with neither a reserved form nor an
    already-assigned key. Reserving first means a genuine printed key never
    gets renamed just because a duplicate elsewhere bumped into it: model keys
    `"5"`, `"5"`, `"5-2"` yield `5`, `5-3`, `5-2`.
    """
    first_at: dict[str, int] = {}
    for index, form in enumerate(forms):
        first_at.setdefault(form, index)
    reserved = set(forms)
    taken: set[str] = set()
    assigned: list[str] = []
    for index, form in enumerate(forms):
        if first_at[form] == index:
            assigned.append(form)
            taken.add(form)
            continue
        suffix = 2
        while f"{form}-{suffix}" in reserved or f"{form}-{suffix}" in taken:
            suffix += 1
        assigned.append(f"{form}-{suffix}")
        taken.add(f"{form}-{suffix}")
    return tuple(assigned)


def _normalize_areas(raw_areas: Sequence[dict[str, Any]], page_count: int) -> tuple[SurveyArea, ...]:
    forms = [
        canonical_slug(cast(str, entry["key"])) or f"area-{position}"
        for position, entry in enumerate(raw_areas, start=1)
    ]
    areas: list[SurveyArea] = []
    for entry, key in zip(raw_areas, _assign_unique(forms), strict=True):
        raw_key = cast(str, entry["key"])
        areas.append(
            SurveyArea(
                key=key,
                name=cast(str, entry["name"]),
                source_label=raw_key if raw_key and raw_key != key else None,
                kind=cast(AreaKind, entry["kind"]),
                source_pages=clamp_pages(cast(list[int], entry["source_pages"]), page_count),
            )
        )
    return tuple(areas)


def _normalize_levels(raw_levels: Sequence[dict[str, Any]], page_count: int) -> tuple[SurveyLevel, ...]:
    numbers = [cast(int, entry["number"]) for entry in raw_levels]
    if any(number < 1 for number in numbers) or len(set(numbers)) != len(numbers):
        numbers = list(range(1, len(raw_levels) + 1))
    return tuple(
        SurveyLevel(
            number=number,
            map_pages=clamp_pages(cast(list[int], entry["map_pages"]), page_count),
            areas=_normalize_areas(cast(list[dict[str, Any]], entry["areas"]), page_count),
        )
        for entry, number in zip(raw_levels, numbers, strict=True)
    )


def normalize_survey(raw: dict[str, Any], page_count: int) -> SurveyIndex:
    """Normalize a schema-valid survey answer into the canonical index, deterministically.

    Dungeon ids are the slug of the model's `name` (empty slug falls back to
    `dungeon-<position>`, 1-based document order); area keys are the slug of
    the model's key (empty falls back to `area-<position>` within the level).
    Collisions resolve by reserve-then-bump (see the phase 1 plan). Level
    numbers invalid or non-unique within a dungeon are renumbered 1..n in
    listed order. Page references outside 1..page_count are dropped; page
    lists are deduplicated and sorted. `source_label` preserves the model's
    original key wherever the canonical form differs; the human-facing `name`
    is never touched. A dungeon the model gave zero levels is dropped —
    unreachable through a conforming provider (the schema requires one).

    Args:
        raw: The model's answer, already validated against
            [`SURVEY_SCHEMA`][osrforge.survey.SURVEY_SCHEMA].
        page_count: The source's page count, for page clamping.

    Returns:
        The canonical survey index.

    Raises:
        ExtractionError: If normalization yields zero dungeons or zero areas —
            a dead conversion; osrlib requires at least one dungeon.
    """
    raw_dungeons = [entry for entry in cast(list[dict[str, Any]], raw["dungeons"]) if entry["levels"]]
    id_forms = [
        canonical_slug(cast(str, entry["name"])) or f"dungeon-{position}"
        for position, entry in enumerate(raw_dungeons, start=1)
    ]
    dungeons = tuple(
        SurveyDungeon(
            id=dungeon_id,
            name=cast(str, entry["name"]),
            levels=_normalize_levels(cast(list[dict[str, Any]], entry["levels"]), page_count),
        )
        for entry, dungeon_id in zip(raw_dungeons, _assign_unique(id_forms), strict=True)
    )
    if not dungeons:
        raise ExtractionError("the survey found no dungeons — nothing to convert")
    if not any(level.areas for dungeon in dungeons for level in dungeon.levels):
        raise ExtractionError("the survey found no keyed areas — nothing to convert")
    town = cast(dict[str, Any], raw["town"])
    return SurveyIndex(
        title=cast(str, raw["title"]),
        hooks=tuple(cast(list[str], raw["hooks"])),
        town=TownInfo(name=cast(str, town["name"]), description=cast(str, town["description"])),
        dungeons=dungeons,
        monster_names=tuple(cast(list[str], raw["monster_names"])),
    )


def filter_index_to_pages(index: SurveyIndex, page_numbers: Iterable[int]) -> SurveyIndex:
    """Restrict every page reference in an index to the given pages.

    This is the excerpt-recording closure step: the extraction runner's
    excerpt mode filters the normalized index down to the committed page
    subset before batch planning, and both the recorder and the replay test
    source page parts exclusively from the committed pages — so an in-range
    model reference to an uncommitted page cannot make a batch request
    unbuildable at replay time.

    Args:
        index: The normalized survey index.
        page_numbers: The pages that are actually available.

    Returns:
        A copy with every `source_pages`/`map_pages` intersected with
        `page_numbers`.
    """
    available = set(page_numbers)
    dungeons = tuple(
        dungeon.model_copy(
            update={
                "levels": tuple(
                    level.model_copy(
                        update={
                            "map_pages": tuple(page for page in level.map_pages if page in available),
                            "areas": tuple(
                                area.model_copy(
                                    update={
                                        "source_pages": tuple(page for page in area.source_pages if page in available)
                                    }
                                )
                                for area in level.areas
                            ),
                        }
                    )
                    for level in dungeon.levels
                )
            }
        )
        for dungeon in index.dungeons
    )
    return index.model_copy(update={"dungeons": dungeons})


def survey(workdir: Workdir, provider: ModelProvider) -> SurveyIndex:
    """Run stage 1: survey the whole module and write `stages/survey.json`.

    Stale `stages/areas.*.json` caches and `stages/monsters.json` are deleted
    only on success — a re-run survey can change canonical ids and the
    encounter-name population, orphaning the downstream caches, but a transient
    provider failure on a re-run leaves the previous consistent cache set
    intact.

    Args:
        workdir: A workdir whose preprocess stage is `completed`.
        provider: The model provider.

    Returns:
        The normalized survey index, as written to the cache.

    Raises:
        ValueError: If the preprocess stage is not `completed` (programmer
            misuse).
        ExtractionError: If the source exceeds the `survey_max_pages` guard
            (before any model call), or the survey finds no dungeons or areas.
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
    """
    run = workdir.read_run()
    preprocess_status = run.stages.get(Stage.PREPROCESS)
    if preprocess_status is None or preprocess_status.status != "completed":
        raise ValueError("survey requires a completed preprocess stage")
    if run.page_count > run.settings.survey_max_pages:
        raise ExtractionError(
            f"source has {run.page_count} pages, over the {run.settings.survey_max_pages}-page survey guard "
            "(survey chunking is a later phase)"
        )
    with track_stage(workdir, Stage.SURVEY) as tracker:
        request = build_survey_request(page_request_parts(workdir, range(1, run.page_count + 1)))
        response = provider.generate(request)
        tracker.add_usage(response.usage)
        tracker.set_model(type(provider).__name__, response.model_id)
        index = normalize_survey(cast(dict[str, Any], response.data), run.page_count)
        workdir.stages_dir.mkdir(parents=True, exist_ok=True)
        for stale in workdir.area_caches():
            stale.unlink()
        workdir.monsters_json.unlink(missing_ok=True)
        write_json_artifact(workdir.survey_json, index)
    return index
