"""Stage 3: monster resolution — extracted names against osrlib's shipped catalog.

The resolution population is the union of encounter monster names across every
`stages/areas.*.json` cache — the names assembly must map — not
`survey.monster_names`: the survey list is a document-wide superset that
includes wandering-table entries and townsfolk, and resolving those would burn
LLM budget on names no keyed encounter references and emit flags nobody can
act on.

The four tiers, exactly the spec's, each consulted only when the previous one
misses: normalized exact match over derived match forms, the curated alias
table, stdlib fuzzy matching, and one LLM pass over the remainder. A fully
deterministic resolution makes no model call.
"""

from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any, cast

from osrlib.core.monsters import MonsterCatalog, MonsterTemplate
from osrlib.data import load_monsters

from osrforge.contracts.run import Stage
from osrforge.contracts.stages import LevelContent, MonsterResolution, MonsterResolutions, SurveyIndex
from osrforge.providers.base import ModelProvider, ModelRequest, TextPart
from osrforge.workdir import Workdir, track_stage, write_json_artifact

__all__ = [
    "MONSTERS_SYSTEM",
    "MONSTER_ALIASES",
    "build_monsters_request",
    "deterministic_resolutions",
    "llm_candidates",
    "monsters",
    "monsters_schema",
    "normalize_monster_name",
]

MONSTERS_SYSTEM = """\
You match monster names extracted from a tabletop adventure module against a fixed monster catalog. The user \
message lists the extracted names, each with its candidate templates as `id (Printed Name)` pairs.

For each name, pick the template a referee would treat as the same creature under another name — edition \
synonyms, spelling variants, singular versus plural. Answer null when no candidate is that creature: never \
pick a merely similar monster, a different creature of the same theme, or a "close enough" substitute.
"""

MONSTER_ALIASES: dict[str, str] = {
    # Observed in JN1 The Chaotic Caves (r28): the catalog's plain wolf is "Normal Wolf".
    "wolf": "normal_wolf",
    # Observed in JN1 The Chaotic Caves (r28): irregular plural of "Lizard Man".
    "lizard men": "lizard_man",
}
"""The curated alias tier: normalized extracted name → catalog template id.

Entry rule: only names observed in a real module run, each entry carrying a
source comment. Growing the table after the JN1 monsters fixture was recorded
changes that fixture's request fingerprint whenever a new entry covers a JN1
name — re-recording is the remedy (see `tests/assets/chaotic-caves/README.md`).
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


def _encounter_names(levels: Sequence[LevelContent]) -> list[str]:
    """The normalized resolution population: every keyed encounter name, deduplicated, sorted.

    A name that normalizes to empty is excluded — the frozen phase 1 schema
    does not forbid an empty monster string, and there is nothing to resolve;
    assembly skips the same encounters with a flag.
    """
    names = {
        normalize_monster_name(encounter.monster)
        for level in levels
        for area in level.areas
        for encounter in area.encounters
    }
    return sorted(names - {""})


def monsters(workdir: Workdir, provider: ModelProvider) -> MonsterResolutions:
    """Run stage 3: resolve every keyed encounter name and write `stages/monsters.json`.

    Tiers 1-3 run first; the provider is called only if names remain — a fully
    deterministic resolution makes no model call. An empty name population
    writes an empty cache and completes. The cache is a single atomic artifact;
    no pre-clearing is needed.

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
        names = _encounter_names(levels)
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
        cache_model = MonsterResolutions(resolutions=resolutions)
        workdir.stages_dir.mkdir(parents=True, exist_ok=True)
        write_json_artifact(workdir.monsters_json, cache_model)
    return cache_model
