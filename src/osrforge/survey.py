"""Stage 1: the survey pass — one structured-output request over the whole module, or chunked page windows.

The survey identifies title, hooks, town info, dungeons, levels, keyed areas
with page locations, and every monster name — the index that plans the content
passes. Normalization to canonical ids and keys happens here, at the source:
`contracts/report.py` pins the address grammar, and the content stage's cache
filenames and per-batch key enums need canonical forms before content runs.

Sources at or under `survey_max_pages` pages survey in one request whose bytes
are identical to what every committed fixture was recorded against — chunking
is purely additive. Larger sources split into contiguous page windows of that
size, each request carrying a window-naming preamble, and the windows' raw
answers merge deterministically ([`merge_survey_answers`][osrforge.survey.merge_survey_answers])
before one `normalize_survey` pass.

Every system-prompt rule is pinned against an observed failure in the phase 0
spike fixtures; see the phase 1 plan before changing one.
"""

import copy
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
    "build_chunked_survey_request",
    "build_survey_request",
    "canonical_slug",
    "filter_index_to_pages",
    "merge_survey_answers",
    "normalize_survey",
    "survey",
    "survey_windows",
]

SURVEY_SYSTEM = """\
You survey tabletop adventure modules. The user message interleaves every page's extracted text (each headed \
by a [page N] marker) with that page's image. Fill the survey schema from those pages.

Rules:
- A dungeon is a keyed adventuring site: caves, ruins, lairs, and the like. A dungeon exists only where the \
module prints a keyed area list for it — lettered or unkeyed callouts on another site's map are that site's \
features, never sites of their own. The town or home base and its buildings are never dungeons — describe \
them only in "town". Leave "town.name" empty only when the module genuinely leaves the town unnamed.
- One dungeon per independently keyed site: first enumerate the module's independently keyed sites, then \
emit exactly that many dungeons. Maps connected internally by stairs or shafts are levels of one dungeon; \
separate lairs or sites with their own maps and entrances are separate dungeons, even when they are drawn \
together on one regional map or share a running area-number sequence (a module keying "A. Orc Lair", \
"B. Goblin Lair" describes separate dungeons — one dungeon each, never merged).
- "description" is the module's own pitch: an excerpt of its printed introduction or back-cover text, quoted \
or tightened from the module's own words — never invented. Leave it empty when the module states none.
- "town.services" lists the named establishments and services the module states the town offers (an inn, a \
temple, a general store). List only what the module states.
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
        "description": {"type": "string"},
        "hooks": {"type": "array", "items": {"type": "string"}},
        "town": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "services": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["name", "description", "services"],
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
    "required": ["title", "description", "hooks", "town", "dungeons", "monster_names"],
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


def build_chunked_survey_request(
    parts: Sequence[TextPart | ImagePart], first_page: int, last_page: int, page_count: int
) -> ModelRequest:
    """Build one chunked-survey window's request: the survey prompt plus a window-naming preamble.

    The preamble is appended only here — the single-request path goes through
    [`build_survey_request`][osrforge.survey.build_survey_request] untouched,
    which is what keeps every committed fixture valid. The schema and tag are
    the single-request path's; distinct windows still produce distinct request
    fingerprints because their pages (and this preamble) differ.

    Args:
        parts: The window's interleaved page parts, from
            [`page_request_parts`][osrforge.pages.page_request_parts].
        first_page: The window's first page (1-based, absolute).
        last_page: The window's last page (1-based, absolute).
        page_count: The whole source's page count.

    Returns:
        The request, tagged `survey`.
    """
    preamble = (
        f"\nThis request carries pages {first_page}-{last_page} of a {page_count}-page module; the other pages "
        "arrive in separate requests. Survey only what these pages show — report the dungeons, levels, keyed "
        "areas, and monster names these pages describe, and leave out anything they don't.\n"
    )
    return ModelRequest(tag="survey", system=SURVEY_SYSTEM + preamble, parts=tuple(parts), schema=SURVEY_SCHEMA)


def survey_windows(page_count: int, window_size: int) -> tuple[tuple[int, int], ...]:
    """Split pages 1..page_count into contiguous, disjoint windows of at most `window_size` pages.

    No overlap, pinned: overlapping windows would double-extract boundary areas
    and force the merge to arbitrate conflicting duplicates of the same key; a
    dungeon spanning a boundary is already covered because each window reports
    the parts it saw and the merge unions them.

    Args:
        page_count: The source's page count.
        window_size: The window size (`survey_max_pages`).

    Returns:
        `(first, last)` page pairs, 1-based and inclusive: `(1, K)`,
        `(K+1, 2K)`, … A source at or under `window_size` yields one window.
    """
    return tuple((first, min(first + window_size - 1, page_count)) for first in range(1, page_count + 1, window_size))


def _first_nonempty(values: Iterable[str]) -> str:
    return next((value for value in values if value), "")


def _union_into(accumulated: list[Any], incoming: Iterable[Any]) -> None:
    """Append unseen items in incoming order — union in first-seen order."""
    seen = set(accumulated)
    for item in incoming:
        if item not in seen:
            accumulated.append(item)
            seen.add(item)


def _merge_town(answers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The town joins as a unit: first entry with a name, else first with a description, else empty.

    The unit rule carries `services` with the chosen entry — the window that
    saw the town section saw its establishments too, and stitching one
    window's name onto another's services would fabricate a town no window
    reported.
    """
    towns = [cast(dict[str, Any], answer["town"]) for answer in answers]
    for town in towns:
        if town["name"]:
            return copy.deepcopy(town)
    for town in towns:
        if town["description"]:
            return copy.deepcopy(town)
    return {"name": "", "description": "", "services": []}


def _merge_areas(accumulated: list[dict[str, Any]], incoming: Sequence[dict[str, Any]]) -> None:
    """Join one window's areas into an accumulated level, occurrence-indexed on the key's slug."""
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for area in accumulated:
        slug = canonical_slug(cast(str, area["key"]))
        if slug:
            occurrences.setdefault(slug, []).append(area)
    seen: dict[str, int] = {}
    for area in incoming:
        slug = canonical_slug(cast(str, area["key"]))
        if slug:
            position = seen.get(slug, 0)
            seen[slug] = position + 1
            candidates = occurrences.get(slug, [])
            if position < len(candidates):
                # First occurrence wins every scalar field; pages union.
                _union_into(
                    cast(list[int], candidates[position]["source_pages"]), cast(list[int], area["source_pages"])
                )
                continue
        accumulated.append(copy.deepcopy(area))


def _merge_levels(accumulated: list[dict[str, Any]], incoming: Sequence[dict[str, Any]]) -> None:
    """Join one window's levels into an accumulated dungeon, occurrence-indexed on `number`."""
    occurrences: dict[int, list[dict[str, Any]]] = {}
    for level in accumulated:
        occurrences.setdefault(cast(int, level["number"]), []).append(level)
    seen: dict[int, int] = {}
    for level in incoming:
        number = cast(int, level["number"])
        position = seen.get(number, 0)
        seen[number] = position + 1
        candidates = occurrences.get(number, [])
        if position < len(candidates):
            target = candidates[position]
            _union_into(cast(list[int], target["map_pages"]), cast(list[int], level["map_pages"]))
            _merge_areas(cast(list[dict[str, Any]], target["areas"]), cast(list[dict[str, Any]], level["areas"]))
        else:
            accumulated.append(copy.deepcopy(level))


def _merge_dungeons(accumulated: list[dict[str, Any]], incoming: Sequence[dict[str, Any]]) -> None:
    """Join one window's dungeons into the accumulator, occurrence-indexed on the name's slug.

    The occurrence lists are snapshotted before this window is processed, so
    entries from the same window can never join each other: a window's n-th
    occurrence of a slug either joins the accumulator's pre-existing n-th
    occurrence or appends, and an appended entry is invisible to its own
    window's later occurrences.
    """
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for dungeon in accumulated:
        slug = canonical_slug(cast(str, dungeon["name"]))
        if slug:
            occurrences.setdefault(slug, []).append(dungeon)
    seen: dict[str, int] = {}
    for dungeon in incoming:
        slug = canonical_slug(cast(str, dungeon["name"]))
        if slug:
            position = seen.get(slug, 0)
            seen[slug] = position + 1
            candidates = occurrences.get(slug, [])
            if position < len(candidates):
                target = candidates[position]
                _merge_levels(
                    cast(list[dict[str, Any]], target["levels"]), cast(list[dict[str, Any]], dungeon["levels"])
                )
                continue
        accumulated.append(copy.deepcopy(dungeon))


def merge_survey_answers(answers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Merge chunked-survey windows' schema-valid raw answers into one raw answer, deterministically.

    The merge happens at the raw level so the merged dict flows through
    [`normalize_survey`][osrforge.survey.normalize_survey] exactly once —
    merging normalized indexes would fight reserve-then-bump uniquing (a key
    bumped to `5-2` in one window and not the other could never re-join).

    Join rules, pinned: entries from the same window never join with each
    other — intra-window multiplicity is preserved verbatim (duplicate slugs
    denote genuinely distinct entities under reserve-then-bump). Across
    windows, entities join occurrence-indexed: a later window's *n*-th
    occurrence of a join key joins the accumulator's *n*-th occurrence, and
    occurrences past the accumulator's count append as new entries. Join keys:
    dungeons on `canonical_slug(name)`, levels on `number` within a joined
    dungeon, areas on `canonical_slug(key)` within a joined level. An empty
    slug never joins — two empty slugs carry no evidence of identity, so each
    empty-slug entry stays distinct and takes `normalize_survey`'s positional
    fallback. On a join, the first occurrence wins every scalar field;
    `source_pages` and `map_pages` union in first-seen order. `title`,
    `description`, and `town` take the first non-empty occurrence in window
    order (`town` as a unit: first entry with a non-empty name, else first
    with a non-empty description, else empty — `services` riding with the
    chosen entry); `hooks` concatenate deduplicated by exact string;
    `monster_names` union in first-seen order.

    Args:
        answers: The windows' raw answers in window order, each already
            validated against [`SURVEY_SCHEMA`][osrforge.survey.SURVEY_SCHEMA].
            The inputs are not mutated.

    Returns:
        One merged raw answer, shaped exactly like a single window's.
    """
    dungeons: list[dict[str, Any]] = []
    hooks: list[str] = []
    monster_names: list[str] = []
    for answer in answers:
        _union_into(hooks, cast(list[str], answer["hooks"]))
        _union_into(monster_names, cast(list[str], answer["monster_names"]))
        _merge_dungeons(dungeons, cast(list[dict[str, Any]], answer["dungeons"]))
    return {
        "title": _first_nonempty(cast(str, answer["title"]) for answer in answers),
        "description": _first_nonempty(cast(str, answer["description"]) for answer in answers),
        "hooks": hooks,
        "town": _merge_town(answers),
        "dungeons": dungeons,
        "monster_names": monster_names,
    }


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
        description=cast(str, raw["description"]),
        hooks=tuple(cast(list[str], raw["hooks"])),
        town=TownInfo(
            name=cast(str, town["name"]),
            description=cast(str, town["description"]),
            services=tuple(cast(list[str], town["services"])),
        ),
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
    """Run stage 1: survey the module and write `stages/survey.json`.

    A source at or under `survey_max_pages` pages surveys in one request built
    by [`build_survey_request`][osrforge.survey.build_survey_request] —
    byte-identical to the pre-chunking request, which the committed fixture
    replay gates prove. A larger source surveys in
    [`survey_windows`][osrforge.survey.survey_windows]-sized chunks whose raw
    answers merge through
    [`merge_survey_answers`][osrforge.survey.merge_survey_answers] before the
    one `normalize_survey` pass. Page markers stay absolute in both modes, so
    downstream stages see the same page-number space either way.

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
        ExtractionError: If the survey finds no dungeons or areas.
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
    """
    run = workdir.read_run()
    preprocess_status = run.stages.get(Stage.PREPROCESS)
    if preprocess_status is None or preprocess_status.status != "completed":
        raise ValueError("survey requires a completed preprocess stage")
    with track_stage(workdir, Stage.SURVEY) as tracker:
        windows = survey_windows(run.page_count, run.settings.survey_max_pages)
        answers: list[dict[str, Any]] = []
        for first_page, last_page in windows:
            parts = page_request_parts(workdir, range(first_page, last_page + 1))
            if len(windows) == 1:
                request = build_survey_request(parts)
            else:
                request = build_chunked_survey_request(parts, first_page, last_page, run.page_count)
            response = provider.generate(request)
            tracker.add_usage(response.usage)
            tracker.set_model(type(provider).__name__, response.model_id)
            answers.append(cast(dict[str, Any], response.data))
        raw = answers[0] if len(windows) == 1 else merge_survey_answers(answers)
        index = normalize_survey(raw, run.page_count)
        workdir.stages_dir.mkdir(parents=True, exist_ok=True)
        for stale in workdir.area_caches():
            stale.unlink()
        workdir.monsters_json.unlink(missing_ok=True)
        workdir.statblocks_json.unlink(missing_ok=True)
        write_json_artifact(workdir.survey_json, index)
    return index
