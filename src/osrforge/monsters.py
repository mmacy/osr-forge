"""Stage 3: monster resolution — extracted names against osrlib's shipped catalog, plus the stat-block pass.

The resolution population is the union of encounter monster names across every
`stages/areas.*.json` cache — the names assembly must map — not
`survey.monster_names`: the survey list is a document-wide superset that
includes wandering-table entries and townsfolk, and resolving those would burn
LLM budget on names no keyed encounter references and emit flags nobody can
act on.

The four [resolution tiers][resolution-tiers], each consulted only when the
previous one misses: normalized exact match over derived match forms, the
curated alias table, stdlib fuzzy matching, and one LLM pass over the
remainder. A fully deterministic resolution makes no model call.

The [stat-block pass][stat-block-pass] runs after the tiers, over exactly the names
still unresolved, gated by the `custom_monsters` knob: one transcription
request per name over its planned page set, cached raw in
`stages/statblocks.json` for assembly's deterministic template mapping. The
pass transcribes — every rules judgment lives in assembly, where it is
testable and correctable.
"""

import re
from collections.abc import Mapping, Sequence
from difflib import SequenceMatcher
from typing import Any, cast

from osrlib.core.monsters import MonsterCatalog, MonsterTemplate
from osrlib.data import load_monsters

from osrforge.contracts.run import Stage
from osrforge.contracts.stages import (
    AC_NOTATIONS,
    AcNotation,
    LevelContent,
    MonsterResolution,
    MonsterResolutions,
    RawStatBlock,
    StatBlocks,
    SurveyIndex,
)
from osrforge.pages import page_request_parts
from osrforge.providers.base import ImagePart, ModelProvider, ModelRequest, TextPart
from osrforge.workdir import Workdir, track_stage, write_json_artifact

__all__ = [
    "MONSTERS_SYSTEM",
    "MONSTER_ALIASES",
    "STATBLOCK_PAGE_CAP",
    "STATBLOCK_SYSTEM",
    "build_monsters_request",
    "build_statblock_request",
    "deterministic_resolutions",
    "encounter_names",
    "llm_candidates",
    "monsters",
    "monsters_schema",
    "normalize_monster_name",
    "parse_statblock_response",
    "statblock_page_plan",
    "statblock_schema",
    "statblock_tag",
]

MONSTERS_SYSTEM = """\
You match monster names extracted from a tabletop adventure module against a fixed monster catalog. The user \
message lists the extracted names, each with its candidate templates as `id (Printed Name)` pairs.

For each name, pick the template a referee would treat as the same creature under another name — edition \
synonyms, spelling variants, singular versus plural. Answer null when no candidate is that creature: never \
pick a merely similar monster, a different creature of the same theme, or a "close enough" substitute. When \
in doubt, answer null — an unmatched name keeps the module's own printed stat block downstream, while a \
wrong pick silently replaces the module's creature with a different one.
"""

MONSTER_ALIASES: dict[str, str] = {
    # Observed in JN1 The Chaotic Caves (r28): the catalog's plain wolf is "Normal Wolf".
    "wolf": "normal_wolf",
    # Observed in JN1 The Chaotic Caves (r28): irregular plural of "Lizard Man".
    "lizard men": "lizard_man",
}
"""The curated alias tier: normalized extracted name → catalog template id.

Entry rule: only names observed in a real module run, each entry carrying a
source comment. Growing the table after a resolution fixture was recorded
changes that fixture's request fingerprint whenever a new entry covers one of
its names — re-recording is the remedy
([the re-record rule][the-re-record-rule]).
"""


def normalize_monster_name(text: str) -> str:
    """Normalize an extracted monster name for resolution and cache keying.

    Casefold, collapse every internal whitespace run to one space, strip. Not
    the slug function — override keys keep their spaces (`"hobgoblin chieftain"`).

    Args:
        text: The name as extracted.

    Returns:
        The normalized name.
    """
    return " ".join(text.casefold().split())


def _squash(form: str) -> str:
    return form.replace(" ", "")


def _template_match_forms(template: MonsterTemplate) -> frozenset[str]:
    """Derive one template's match forms from its irregularly named catalog entry.

    The catalog's naming follows no single rule — some names are comma-inverted
    (`Centipede, Giant`), some compound where modules print one word
    (`Owl Bear` vs `owlbear`) — so each template contributes its normalized
    name, the comma-inverted rearrangement, its id with separators as spaces,
    and the separator-squashed spellings of all of those.
    """
    forms = {normalize_monster_name(template.name), normalize_monster_name(template.id.replace("_", " "))}
    name = normalize_monster_name(template.name)
    head, separator, tail = name.partition(",")
    if separator:
        forms.add(normalize_monster_name(f"{tail} {head}"))
    return frozenset(forms | {_squash(form) for form in forms})


def _extracted_name_forms(normalized: str) -> frozenset[str]:
    """Derive an extracted name's match forms: itself, naive singulars, squashed spellings."""
    forms = {normalized}
    if normalized.endswith("es"):
        forms.add(normalized[:-2])
    if normalized.endswith("s"):
        forms.add(normalized[:-1])
    return frozenset(forms | {_squash(form) for form in forms})


def _catalog_forms(catalog: MonsterCatalog) -> dict[str, set[str]]:
    """Index the catalog: match form → the template ids contributing it."""
    index: dict[str, set[str]] = {}
    for template in catalog.monsters:
        for form in _template_match_forms(template):
            index.setdefault(form, set()).add(template.id)
    return index


def _fuzzy_scores(normalized: str, catalog: MonsterCatalog) -> dict[str, float]:
    """Score every template: the best ratio between the normalized name and any match form."""
    scores: dict[str, float] = {}
    for template in catalog.monsters:
        best = max(SequenceMatcher(None, normalized, form).ratio() for form in _template_match_forms(template))
        scores[template.id] = best
    return scores


def deterministic_resolutions(
    names: Sequence[str], catalog: MonsterCatalog, fuzzy_threshold: float
) -> dict[str, MonsterResolution]:
    """Run tiers 1-3 (exact, alias, fuzzy) over normalized names.

    Exact matches only when the matched template is unique — the shipped
    catalog has no match-form collisions (tested), but an ambiguous hit falls
    through to the later tiers rather than picking arbitrarily. Fuzzy
    auto-accepts only when the best score clears `fuzzy_threshold` *and* the
    best template is unique at that score; a tie goes to the LLM tier rather
    than a coin flip.

    Args:
        names: The normalized names to resolve.
        catalog: The osrlib monster catalog.
        fuzzy_threshold: The fuzzy tier's auto-accept floor.

    Returns:
        Resolutions for exactly the names tiers 1-3 resolved.
    """
    forms_index = _catalog_forms(catalog)
    resolutions: dict[str, MonsterResolution] = {}
    for name in names:
        hits: set[str] = set()
        for form in _extracted_name_forms(name):
            hits |= forms_index.get(form, set())
        if len(hits) == 1:
            resolutions[name] = MonsterResolution(template_id=next(iter(hits)), method="exact")
            continue
        alias = MONSTER_ALIASES.get(name)
        if alias is not None:
            resolutions[name] = MonsterResolution(template_id=alias, method="alias")
            continue
        scores = _fuzzy_scores(name, catalog)
        best = max(scores.values())
        best_ids = [template_id for template_id, score in scores.items() if score == best]
        if best >= fuzzy_threshold and len(best_ids) == 1:
            resolutions[name] = MonsterResolution(template_id=best_ids[0], method="fuzzy")
    return resolutions


def llm_candidates(name: str, catalog: MonsterCatalog, top_k: int) -> tuple[tuple[str, str], ...]:
    """Return one name's LLM-tier candidates as `(template_id, printed_name)` pairs.

    Ordered by fuzzy score descending, ties broken by template id ascending,
    the top-k cut taken after that ordering — candidate identity and order are
    part of the frozen fixture's request fingerprint, so they must be
    deterministic.

    Args:
        name: The normalized extracted name.
        catalog: The osrlib monster catalog.
        top_k: How many candidates to offer.

    Returns:
        The top-k candidates.
    """
    scores = _fuzzy_scores(name, catalog)
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    names_by_id = {template.id: template.name for template in catalog.monsters}
    return tuple((template_id, names_by_id[template_id]) for template_id, _ in ordered[:top_k])


def monsters_schema(names_with_candidates: Sequence[tuple[str, Sequence[tuple[str, str]]]]) -> dict[str, object]:
    """Build the LLM tier's JSON Schema: one required property per name, enum-locked to its candidates.

    The proven dynamic-schema pattern from content's key enums: the model
    cannot invent, misspell, or skip a name, and each answer is one of that
    name's candidate ids or null.

    Args:
        names_with_candidates: `(normalized name, candidates)` pairs, already
            in sorted-name order.

    Returns:
        The request schema.
    """
    properties: dict[str, object] = {}
    for name, candidates in names_with_candidates:
        ids: list[str | None] = [template_id for template_id, _ in candidates]
        properties[name] = {
            "type": "object",
            "properties": {"template_id": {"type": ["string", "null"], "enum": [*ids, None]}},
            "required": ["template_id"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": [name for name, _ in names_with_candidates],
        "additionalProperties": False,
    }


def build_monsters_request(
    names_with_candidates: Sequence[tuple[str, Sequence[tuple[str, str]]]],
) -> ModelRequest:
    """Build the monsters-stage request over names and their candidate lists.

    Public and pure, like [`build_survey_request`][osrforge.survey.build_survey_request]
    — the runner and fixture tests must build fingerprint-identical requests
    without duplicating prompt code. Names are sorted ascending here, so the
    prompt, the schema's property order, and its `required` list always agree
    regardless of the caller's order. The request carries no page images, only
    text — which is what makes its fixture replay-grade.

    Args:
        names_with_candidates: `(normalized name, candidates)` pairs; each
            candidate is `(template_id, printed_name)`.

    Returns:
        The request, tagged `monsters`.

    Raises:
        ValueError: If no names are given — the stage never builds an empty
            request (programmer misuse).
    """
    if not names_with_candidates:
        raise ValueError("the monsters request needs at least one unresolved name")
    ordered = sorted(names_with_candidates, key=lambda item: item[0])
    lines: list[str] = []
    for name, candidates in ordered:
        listed = ", ".join(f"{template_id} ({printed})" for template_id, printed in candidates)
        lines.append(f'- "{name}": candidates {listed}')
    text = "Match each extracted monster name to one of its candidates, or null:\n" + "\n".join(lines) + "\n"
    return ModelRequest(
        tag="monsters",
        system=MONSTERS_SYSTEM,
        parts=(TextPart(text=text),),
        schema=monsters_schema(ordered),
    )


def encounter_names(levels: Sequence[LevelContent]) -> list[str]:
    """Return the normalized resolution population: every keyed encounter name, deduplicated, sorted.

    A name that normalizes to empty is excluded — the
    [frozen stage-cache schema][frozen-schema] does not forbid an empty
    monster string, and there is nothing to resolve;
    assembly skips the same encounters with a flag. Public because it is *the*
    population rule: the stage, assembly's stale-cache check, and the
    extraction runner must all agree on it, or a recorded fixture's request
    fingerprint could drift from what the stage builds.

    Args:
        levels: The content caches to collect names from.

    Returns:
        The normalized names, deduplicated and ascending.
    """
    names = {
        normalize_monster_name(encounter.monster)
        for level in levels
        for area in level.areas
        for encounter in area.encounters
    }
    return sorted(names - {""})


STATBLOCK_PAGE_CAP = 8
"""The stat-block pass's per-name page budget: encounter pages first, then ascending text hits."""

STATBLOCK_SYSTEM = """\
You transcribe one creature's printed stat block from tabletop adventure module pages. The user message \
names the creature, then interleaves each page's extracted text (each headed by a [page N] marker) with \
that page's image — check the images too: tabular stat blocks often scramble in the extracted text.

Rules:
- Transcribe, never convert. Copy each value as the page prints it, in its printed notation. Do not derive \
missing values, translate between editions or armour-class systems, or fill gaps from your own knowledge \
of any game. A value the pages don't print is null.
- "found" is whether the pages print a stat block (or an inline stat line) for this creature. When false, \
leave every other field null or empty.
- "ac" is the armour-class value exactly as printed; "ac_notation" classifies the printed system: \
"descending" (classic, lower is better), "ascending" (modern, higher is better), or "dual" (both printed, \
like "5 [14]").
- A Hit Dice line as printed goes in "hit_dice" ("3+1", "1-1", "½", "2d8"); a class-and-level designation \
("F 3", "3rd-level cleric", "Thief 2") goes in "class_level". Use whichever the block prints; both null \
means the block states neither.
- "thac0" is the printed to-hit line, keeping its notation ("17", "19 [+0]", "+2").
- "attacks" keeps one entry per printed attack line, with counts and damage as printed \
("2 claws (1d4 each)", "1 bite (1d6 + poison)").
- "movement" is the printed movement line ("120' (40')", "Fly 180' (60')", "30 ft.").
- "saves" is the printed saving-throw line, whatever its form ("D12 W13 P14 B15 S16 (2)", "save as F2", \
"Fort +2, Ref +4, Will +1").
- "special" keeps one entry per printed special-ability line or note.
- "number_appearing" is the printed number-appearing value ("1d6 (2d6)", "2-8").
- "source_pages" refer to the [page N] markers in this request, never to page numbers printed on the pages.
- "confidence" is your self-assessment in [0, 1] of how faithfully you transcribed the block.
"""


def statblock_tag(name: str) -> str:
    """Return one name's stat-block request tag: `statblock.<slug>` within the tag charset.

    Slugging is lossy (`"orc chief"` and `"orc-chief"` share a tag), which is
    harmless: fixture filenames append the request fingerprint, so identity
    never rests on the tag alone.

    Args:
        name: The normalized monster name.

    Returns:
        The request tag.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return f"statblock.{slug or 'unnamed'}"


def statblock_page_plan(
    name: str, levels: Sequence[LevelContent], page_texts: Mapping[int, str], cap: int = STATBLOCK_PAGE_CAP
) -> tuple[int, ...]:
    """Plan one unresolved name's page set: encounter pages union text-layer hits, capped.

    The union of the name's encounter `source_pages` (every content-cache area
    with an encounter normalizing to `name`) and every page whose extracted
    text layer contains the name — a deterministic local search (casefolded,
    whitespace collapsed), no model. The search is what catches the
    printed-elsewhere pattern (stat blocks in a new-monsters appendix far from
    the encounter); a scanned module with an empty text layer degrades to
    encounter pages only. The cap keeps encounter pages first, then ascending
    text hits.

    Args:
        name: The normalized monster name.
        levels: The content caches.
        page_texts: Page number → that page's extracted text layer.
        cap: The page budget.

    Returns:
        The planned pages, encounter pages (ascending) then text hits
        (ascending), capped.
    """
    encounter_pages = sorted(
        {
            page
            for level in levels
            for area in level.areas
            if any(normalize_monster_name(encounter.monster) == name for encounter in area.encounters)
            for page in area.source_pages
        }
    )
    hits = sorted(
        page
        for page, text in page_texts.items()
        if page not in encounter_pages and name in " ".join(text.casefold().split())
    )
    return tuple((encounter_pages + hits)[:cap])


def statblock_schema() -> dict[str, object]:
    """Build the stat-block pass's JSON Schema: the printed block, system-neutral, plus the `found` marker.

    Returns:
        The request schema.
    """
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "ac": {"type": ["string", "null"]},
            "ac_notation": {"type": ["string", "null"], "enum": [*AC_NOTATIONS, None]},
            "thac0": {"type": ["string", "null"]},
            "hit_dice": {"type": ["string", "null"]},
            "class_level": {"type": ["string", "null"]},
            "hp": {"type": ["integer", "null"], "minimum": 1},
            "attacks": {"type": "array", "items": {"type": "string"}},
            "movement": {"type": ["string", "null"]},
            "saves": {"type": ["string", "null"]},
            "morale": {"type": ["integer", "null"], "minimum": 2, "maximum": 12},
            "alignment": {"type": ["string", "null"]},
            "xp": {"type": ["integer", "null"], "minimum": 0},
            "number_appearing": {"type": ["string", "null"]},
            "special": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "source_pages": {"type": "array", "items": {"type": "integer"}},
        },
        "required": [
            "found",
            "ac",
            "ac_notation",
            "thac0",
            "hit_dice",
            "class_level",
            "hp",
            "attacks",
            "movement",
            "saves",
            "morale",
            "alignment",
            "xp",
            "number_appearing",
            "special",
            "confidence",
            "source_pages",
        ],
        "additionalProperties": False,
    }


def build_statblock_request(name: str, parts: Sequence[TextPart | ImagePart]) -> ModelRequest:
    """Build one name's stat-block transcription request.

    Public and pure, like [`build_monsters_request`][osrforge.monsters.build_monsters_request]
    — the runner and fixture tests must build fingerprint-identical requests
    without duplicating prompt code. Pages ride as text *and* images: the one
    stat-block form the text layer destroys is the tabular one.

    Args:
        name: The normalized monster name.
        parts: The planned pages' interleaved parts
            ([`page_request_parts`][osrforge.pages.page_request_parts]).

    Returns:
        The request, tagged `statblock.<slug>`.

    Raises:
        ValueError: If no parts are given — a name with no planned pages never
            builds a request (programmer misuse; the stage writes an absent
            marker instead).
    """
    if not parts:
        raise ValueError("the stat-block request needs at least one page part")
    header = f'Transcribe the printed stat block for the creature "{name}" from the pages below.\n'
    return ModelRequest(
        tag=statblock_tag(name),
        system=STATBLOCK_SYSTEM,
        parts=(TextPart(text=header), *parts),
        schema=statblock_schema(),
    )


def parse_statblock_response(data: object) -> RawStatBlock | None:
    """Parse one schema-valid stat-block response; `found: false` is the explicit absent marker.

    Public for the same reason the request builders are: the extraction
    runner's recording session must produce a cache byte-identical to the
    stage's without duplicating parsing code.

    Args:
        data: The response data, already validated against
            [`statblock_schema`][osrforge.monsters.statblock_schema].

    Returns:
        The raw block, or `None` — the explicit absent marker.
    """
    answer = cast(dict[str, Any], data)
    if not cast(bool, answer["found"]):
        return None
    return RawStatBlock(
        ac=cast(str | None, answer["ac"]),
        ac_notation=cast(AcNotation | None, answer["ac_notation"]),
        thac0=cast(str | None, answer["thac0"]),
        hit_dice=cast(str | None, answer["hit_dice"]),
        class_level=cast(str | None, answer["class_level"]),
        hp=cast(int | None, answer["hp"]),
        attacks=tuple(cast(list[str], answer["attacks"])),
        movement=cast(str | None, answer["movement"]),
        saves=cast(str | None, answer["saves"]),
        morale=cast(int | None, answer["morale"]),
        alignment=cast(str | None, answer["alignment"]),
        xp=cast(int | None, answer["xp"]),
        number_appearing=cast(str | None, answer["number_appearing"]),
        special=tuple(cast(list[str], answer["special"])),
        confidence=cast(float, answer["confidence"]),
        source_pages=tuple(cast(list[int], answer["source_pages"])),
    )


def monsters(workdir: Workdir, provider: ModelProvider) -> MonsterResolutions:
    """Run stage 3: resolve every keyed encounter name; write `stages/monsters.json` and `stages/statblocks.json`.

    Tiers 1-3 run first; the provider is called only if names remain — a fully
    deterministic resolution makes no model call. An empty name population
    writes an empty cache and completes. The stat-block pass then runs over
    exactly the names still unresolved (under `custom_monsters: emit`): one
    transcription request per name over its planned page set, with a name
    whose plan is empty (no encounter pages, no text hits) cached as an
    explicit absent marker without a model call. `stages/statblocks.json` is
    rewritten on every run — the knob echo plus an entry per unresolved name;
    under `off`, the echo and an empty `blocks`. Both caches are single atomic
    artifacts; no pre-clearing is needed.

    Args:
        workdir: A workdir whose content stage is `completed`.
        provider: The model provider.

    Returns:
        The resolutions, as written to the cache.

    Raises:
        ValueError: If the content stage is not `completed`, or the survey or
            any level's area cache is missing (programmer misuse).
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
    """
    run = workdir.read_run()
    content_status = run.stages.get(Stage.CONTENT)
    if content_status is None or content_status.status != "completed":
        raise ValueError("monsters requires a completed content stage")
    if not workdir.survey_json.is_file():
        raise ValueError(f"the survey cache is missing: {workdir.survey_json}")
    index = SurveyIndex.model_validate_json(workdir.survey_json.read_text(encoding="utf-8"))
    levels: list[LevelContent] = []
    for dungeon in index.dungeons:
        for level in dungeon.levels:
            cache = workdir.areas_json(dungeon.id, level.number)
            if not cache.is_file():
                raise ValueError(f"a level's content cache is missing: {cache}")
            levels.append(LevelContent.model_validate_json(cache.read_text(encoding="utf-8")))
    catalog = load_monsters()
    with track_stage(workdir, Stage.MONSTERS) as tracker:
        names = encounter_names(levels)
        resolutions = deterministic_resolutions(names, catalog, run.settings.monster_fuzzy_threshold)
        remaining = [name for name in names if name not in resolutions]
        if remaining:
            request = build_monsters_request(
                [(name, llm_candidates(name, catalog, run.settings.monster_llm_top_k)) for name in remaining]
            )
            response = provider.generate(request)
            tracker.add_usage(response.usage)
            tracker.set_model(type(provider).__name__, response.model_id)
            answers = cast(dict[str, dict[str, Any]], response.data)
            for name in remaining:
                template_id = cast(str | None, answers[name]["template_id"])
                if template_id is None:
                    resolutions[name] = MonsterResolution(template_id=None, method="unresolved")
                else:
                    resolutions[name] = MonsterResolution(template_id=template_id, method="llm")
        blocks: dict[str, RawStatBlock | None] = {}
        if run.settings.custom_monsters == "emit":
            unresolved = sorted(name for name, entry in resolutions.items() if entry.template_id is None)
            if unresolved:
                page_texts = {
                    number: workdir.page_txt(number).read_text(encoding="utf-8")
                    for number in range(1, run.page_count + 1)
                }
                for name in unresolved:
                    pages = statblock_page_plan(name, levels, page_texts)
                    if not pages:
                        blocks[name] = None
                        continue
                    response = provider.generate(build_statblock_request(name, page_request_parts(workdir, pages)))
                    tracker.add_usage(response.usage)
                    tracker.set_model(type(provider).__name__, response.model_id)
                    blocks[name] = parse_statblock_response(response.data)
        statblocks = StatBlocks(custom_monsters=run.settings.custom_monsters, blocks=blocks)
        cache_model = MonsterResolutions(resolutions=resolutions)
        workdir.stages_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(workdir.monsters_json, cache_model)
        write_json_artifact(workdir.statblocks_json, statblocks)
    return cache_model
