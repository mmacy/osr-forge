"""Stage 5: assembly — the `Adventure` build, `validate_adventure`, report production, and artifact writing.

Assembly is pure: `adventure.json`, `report.json`, and the previews are a
deterministic function of the cached stage outputs. Overrides are **not** read
in phase 2 — application is phase 3's charter — so until then assembly is a
pure function of the stage caches alone, and `report.json`'s per-area
`overridden` stays empty.

Two sequential trackings: `geometry` around synthesis, then `assemble` around
the build, validation, and artifact writes — so a failure in the second leaves
an honest `geometry: completed`. Neither stage touches a provider; usage stays
zero and `run.json`'s provider identity is untouched.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from osrlib.core.dice import parse as parse_dice
from osrlib.core.items import Coins
from osrlib.core.tables import EncounterTable, MonsterEncounterEntry
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
    DungeonSpec,
    FeatureSpec,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    TrapEffect,
    TrapSpec,
    ValuableSpec,
)
from osrlib.data import load_encounter_tables, load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.versioning import stamp_document

from osrforge.contracts.report import (
    AreaReport,
    ExtractionReport,
    Flag,
    ModuleInfo,
    MonsterSummary,
    ValidationResult,
    format_flag,
)
from osrforge.contracts.run import Stage, TokenUsage
from osrforge.contracts.stages import (
    AreaContent,
    AreaEncounter,
    LevelContent,
    MonsterResolutions,
    SurveyIndex,
)
from osrforge.geometry import LevelGeometry, synthesize_geometry
from osrforge.monsters import encounter_names, normalize_monster_name
from osrforge.previews import render_level_svg
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, track_stage, write_json_artifact

__all__ = ["AssembleResult", "assemble", "build_draft", "parse_treasure", "render_previews"]

# The unanchored dice scan (osrlib's die sizes, optional count): a treasure
# string containing dice notation is per-monster or conditional treasure and
# cannot be a fixed cache.
_DICE_SCAN = re.compile(r"\b(?:[1-9][0-9]{0,2})?d(?:2|3|4|6|8|10|12|20|100)\b")
_EACH_PER = re.compile(r"\b(?:each|per)\b", re.IGNORECASE)
_WORTH = re.compile(r"^(?P<name>.+?)\s+worth\s+(?P<value>[1-9][0-9]*)\s*gp\.?$", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_GEM_WORD = re.compile(r"\bgems?\b", re.IGNORECASE)
_COIN = re.compile(r"([1-9][0-9]*)\s*(cp|sp|ep|gp|pp)\b", re.IGNORECASE)
_TREASURE_TYPE = re.compile(r"^treasure types?\s+([A-Va-v])\.?$", re.IGNORECASE)

_VALIDATION_HEADER = "adventure validation failed:"


@dataclass(frozen=True)
class AssembleResult:
    """The spec's `assemble()` return: the draft adventure plus its report."""

    adventure: Adventure
    report: ExtractionReport


@dataclass(frozen=True)
class ParsedTreasure:
    """One area's treasure strings through the pinned grammar."""

    coins: Coins
    valuables: tuple[ValuableSpec, ...]
    letters: tuple[str, ...]
    unparsed: tuple[str, ...]


def parse_treasure(strings: tuple[str, ...]) -> ParsedTreasure:
    """Parse an area's treasure strings through the pinned, conservative grammar.

    Per string, tried in order, first match wins: dice notation or the words
    `each`/`per` → unparsed (per-monster and conditional treasure cannot be a
    fixed cache); `<thing> worth <N> gp` → a valuable (`gem` exactly when the
    thing names a gem, else `jewellery` — both kinds carry identical value and
    XP semantics, so the narrow lexicon errs harmlessly); money references
    `<N> <cp|sp|ep|gp|pp>` with no digits outside them → coins summed per
    denomination; `treasure type <A-V>` → a generated-treasure letter.
    Anything else is unparsed — assembly flags it and (under `best-effort`)
    compensates with an unguarded-treasure roll. A string that is empty after
    stripping is skipped outright: it carries no information to flag, and the
    frozen phase 1 schema does not forbid it, so it must not crash assembly.

    Args:
        strings: The area's cached treasure strings.

    Returns:
        The parsed pieces; letters and unparsed strings keep derivation order
        (letters deduplicated on first occurrence).
    """
    coins_by_denomination = {"pp": 0, "gp": 0, "ep": 0, "sp": 0, "cp": 0}
    valuables: list[ValuableSpec] = []
    letters: list[str] = []
    unparsed: list[str] = []
    for raw in strings:
        text = raw.strip()
        if not text:
            continue
        if _DICE_SCAN.search(text) or _EACH_PER.search(text):
            unparsed.append(raw)
            continue
        worth = _WORTH.match(text)
        if worth is not None:
            name = _ARTICLE.sub("", worth.group("name")).strip()
            kind = "gem" if _GEM_WORD.search(name) else "jewellery"
            valuables.append(ValuableSpec(kind=kind, name=name, value_gp=int(worth.group("value"))))
            continue
        coin_matches = _COIN.findall(text)
        if coin_matches and not any(character.isdigit() for character in _COIN.sub("", text)):
            for amount, denomination in coin_matches:
                coins_by_denomination[denomination.lower()] += int(amount)
            continue
        typed = _TREASURE_TYPE.match(text)
        if typed is not None:
            letter = typed.group(1).upper()
            if letter not in letters:
                letters.append(letter)
            continue
        unparsed.append(raw)
    return ParsedTreasure(
        coins=Coins(**coins_by_denomination),
        valuables=tuple(valuables),
        letters=tuple(letters),
        unparsed=tuple(unparsed),
    )


def _stand_in_template(name: str, table: EncounterTable) -> str:
    """The best-effort stand-in for one unresolved name: a deterministic, level-banded catalog pick.

    Candidates are the level table's monster rows in d20-roll order
    (`npc_party` rows are skipped — an adventurer party is not a keyed
    monster); the pick hashes the normalized name (sha256 is platform-stable
    and salt-free where Python's built-in `hash()` is neither) so stand-ins
    vary across names but never across runs; the template is the row entry's
    first id — in the shipped data, each pool's lowest variant.
    """
    entries = [row.entry for row in table.rows if isinstance(row.entry, MonsterEncounterEntry)]
    index = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big") % len(entries)
    return entries[index].monster_ids[0]


def _keyed_monster(template_id: str, encounter: AreaEncounter, name: str, flags: list[str]) -> KeyedMonster:
    """Map one encounter's count fields onto osrlib's exactly-one-of rule (the phase 1 pins)."""
    dice = encounter.count_dice
    if dice is not None:
        try:
            parse_dice(dice)
        except ContentValidationError:
            # Unreachable through the extraction schema's DICE_PATTERN (a
            # strict subset of osrlib's grammar) — defense in depth. The cache
            # is never rewritten; the dice are discarded in memory only.
            flags.append(format_flag(Flag.LOW_CONFIDENCE, f"unparseable count for {name}"))
        else:
            return KeyedMonster(template_id=template_id, count_dice=dice)
    if encounter.count_fixed is not None:
        return KeyedMonster(template_id=template_id, count_fixed=encounter.count_fixed)
    flags.append(format_flag(Flag.LOW_CONFIDENCE, f"count unstated for {name}"))
    return KeyedMonster(template_id=template_id, count_fixed=1)


def _build_encounter(
    content: AreaContent,
    resolutions: MonsterResolutions,
    table: EncounterTable,
    settings: ConversionSettings,
    count_flags: list[str],
    monster_flags: list[str],
) -> tuple[KeyedEncounter | None, list[str]]:
    """Merge an area's cache encounters into one `KeyedEncounter`, applying the unresolved fallback.

    Returns the encounter (or `None`) and the area's unresolved names in
    derivation order. An encounter whose name normalizes to empty is skipped
    with a `low_confidence` flag — the frozen phase 1 schema does not forbid an
    empty monster string, there is nothing to resolve or stand in for, and the
    monsters stage excludes it from the resolution population the same way.
    """
    keyed: list[KeyedMonster] = []
    unresolved: list[str] = []
    for encounter in content.encounters:
        name = normalize_monster_name(encounter.monster)
        if not name:
            count_flags.append(format_flag(Flag.LOW_CONFIDENCE, "unnamed encounter"))
            continue
        resolution = resolutions.resolutions.get(name)
        if resolution is None:
            raise ValueError(f"the monsters cache has no resolution for {name!r} — a stale cache; re-run monsters")
        if resolution.template_id is not None:
            keyed.append(_keyed_monster(resolution.template_id, encounter, name, count_flags))
            continue
        unresolved.append(name)
        if settings.unresolved_fallback == "omit":
            monster_flags.append(format_flag(Flag.MONSTER_UNRESOLVED, name))
            continue
        stand_in = _stand_in_template(name, table)
        monster_flags.append(format_flag(Flag.MONSTER_UNRESOLVED, f"{name} → {stand_in}"))
        keyed.append(_keyed_monster(stand_in, encounter, name, count_flags))
    return (KeyedEncounter(monsters=tuple(keyed)) if keyed else None, unresolved)


def _build_area(
    address: str,
    area_key: str,
    area_name: str,
    survey_pages: tuple[int, ...],
    content: AreaContent | None,
    geometry: LevelGeometry,
    resolutions: MonsterResolutions,
    table: EncounterTable,
    settings: ConversionSettings,
) -> tuple[AreaSpec, AreaReport, list[str]]:
    """Build one `AreaSpec` and its report entry; returns them plus the area's unresolved names."""
    cells = geometry.areas[area_key]
    low_confidence: list[str] = []
    monster_flags: list[str] = []
    treasure_flags: list[str] = []
    unresolved: list[str] = []

    if content is None:
        # The placeholder phase 1 pinned: the model skipped the area twice, or
        # the level had no pages — the survey index remains the authority on
        # what exists.
        low_confidence.append(format_flag(Flag.LOW_CONFIDENCE, "not extracted"))
        spec = AreaSpec(id=area_key, name=area_name, description="", cells=cells)
        confidence = 0.0
        source_pages = survey_pages
    else:
        encounter, unresolved = _build_encounter(content, resolutions, table, settings, low_confidence, monster_flags)
        trap = None
        if content.trap is not None:
            trap = TrapSpec(kind="room", trigger="enter", affects="triggerer", effect=TrapEffect(manual=content.trap))
        features = [
            FeatureSpec(id=f"{area_key}-f{number}", kind="custom", description=text)
            for number, text in enumerate(content.features, start=1)
        ]
        parsed = parse_treasure(content.treasure)
        treasure_flags.extend(format_flag(Flag.TREASURE_UNPARSED, text) for text in parsed.unparsed)
        if parsed.coins.total_coins or parsed.valuables:
            features.append(
                FeatureSpec(
                    id=f"{area_key}-treasure",
                    kind="treasure_cache",
                    coins=parsed.coins,
                    valuables=parsed.valuables,
                )
            )
        treasure = None
        if parsed.letters:
            treasure = AreaTreasureSpec(letters=parsed.letters)
        elif parsed.unparsed and settings.unresolved_fallback == "best-effort":
            treasure = AreaTreasureSpec(unguarded=True)
        spec = AreaSpec(
            id=area_key,
            name=area_name,
            description=content.description,
            cells=cells,
            encounter=encounter,
            features=tuple(features),
            trap=trap,
            treasure=treasure,
        )
        confidence = content.confidence
        source_pages = content.source_pages

    connection_flags = [
        format_flag(Flag.CONNECTION_AMBIGUOUS, f"unresolved target {to_key}")
        for key, to_key in geometry.unresolved_connections
        if key == area_key
    ]
    connection_flags.extend(
        format_flag(Flag.CONNECTION_AMBIGUOUS, f"unknown direction to {target}")
        for key, target in geometry.unknown_direction_connections
        if key == area_key
    )
    if area_key in geometry.disconnected_areas:
        connection_flags.append(
            format_flag(Flag.CONNECTION_AMBIGUOUS, "not connected to the entrance in the extracted graph")
        )

    flags = [format_flag(Flag.GEOMETRY_SYNTHESIZED)]
    for group in (low_confidence, monster_flags, connection_flags, treasure_flags):
        flags.extend(dict.fromkeys(group))
    report = AreaReport(id=address, source_pages=source_pages, confidence=confidence, flags=tuple(flags))
    return spec, report, unresolved


@dataclass(frozen=True)
class DraftResult:
    """`build_draft`'s output: the adventure plus everything report production needs from the build."""

    adventure: Adventure
    area_reports: tuple[AreaReport, ...]
    module_flags: tuple[str, ...]
    unresolved: tuple[str, ...]


def build_draft(
    index: SurveyIndex,
    levels: tuple[LevelContent, ...],
    resolutions: MonsterResolutions,
    geometries: tuple[LevelGeometry, ...],
    settings: ConversionSettings,
) -> DraftResult:
    """Build the draft adventure and per-area reports from validated caches — pure.

    Args:
        index: The survey cache.
        levels: Every level's content cache, in survey order.
        resolutions: The monsters cache; every keyed encounter name must have
            an entry.
        geometries: The synthesized geometry, in survey order.
        settings: The run's settings echo (`unresolved_fallback`).

    Returns:
        The draft, its per-area reports in survey order, the module-scope
        flags, and the sorted unresolved names.

    Raises:
        ValueError: If an encounter name is missing from the resolutions — a
            stale cache (`convert`'s ordering makes it unreachable).
    """
    module_flags: list[str] = []
    name = index.title
    if not name:
        name = "Untitled module"
        module_flags.append(format_flag(Flag.LOW_CONFIDENCE, "module title unstated"))
    town_name = index.town.name
    if not town_name:
        town_name = "Town"
        module_flags.append(format_flag(Flag.LOW_CONFIDENCE, "town name unstated"))
    town = TownSpec(name=town_name, description=index.town.description)

    contents = {(level.dungeon_id, level.level_number): level for level in levels}
    geometry_by_address = {(geometry.dungeon_id, geometry.level_number): geometry for geometry in geometries}
    tables = load_encounter_tables()

    dungeons: list[DungeonSpec] = []
    area_reports: list[AreaReport] = []
    unresolved: set[str] = set()
    for survey_dungeon in index.dungeons:
        level_specs: list[LevelSpec] = []
        for survey_level in survey_dungeon.levels:
            geometry = geometry_by_address[(survey_dungeon.id, survey_level.number)]
            content = contents.get((survey_dungeon.id, survey_level.number))
            content_by_key = {area.key: area for area in content.areas} if content is not None else {}
            table = tables.for_level(survey_level.number)
            areas: list[AreaSpec] = []
            for survey_area in survey_level.areas:
                spec, report, area_unresolved = _build_area(
                    address=f"{survey_dungeon.id}/{survey_level.number}/{survey_area.key}",
                    area_key=survey_area.key,
                    area_name=survey_area.name,
                    survey_pages=survey_area.source_pages,
                    content=content_by_key.get(survey_area.key),
                    geometry=geometry,
                    resolutions=resolutions,
                    table=table,
                    settings=settings,
                )
                areas.append(spec)
                area_reports.append(report)
                unresolved.update(area_unresolved)
            level_specs.append(
                LevelSpec(
                    number=survey_level.number,
                    width=geometry.width,
                    height=geometry.height,
                    edges=geometry.edges,
                    areas=tuple(areas),
                    transitions=geometry.transitions,
                    entrance=geometry.entrance,
                )
            )
        dungeons.append(DungeonSpec(id=survey_dungeon.id, name=survey_dungeon.name, levels=tuple(level_specs)))

    adventure = Adventure(name=name, description="", hooks=index.hooks, town=town, dungeons=tuple(dungeons))
    return DraftResult(
        adventure=adventure,
        area_reports=tuple(area_reports),
        module_flags=tuple(module_flags),
        unresolved=tuple(sorted(unresolved)),
    )


def _run_validation(adventure: Adventure) -> ValidationResult:
    """Run osrlib's content gate; findings are report data, never a crash.

    By construction it should pass — geometry's postconditions and the
    never-dangling encounter rule cover every check — but the report records
    what the gate actually said, never an assumption.
    """
    try:
        validate_adventure(adventure, load_monsters(), load_equipment())
    except ContentValidationError as error:
        lines = str(error).splitlines()
        if lines and lines[0] == _VALIDATION_HEADER:
            lines = lines[1:]
        return ValidationResult(passed=False, errors=tuple(lines))
    return ValidationResult(passed=True)


def _load_caches(workdir: Workdir) -> tuple[SurveyIndex, tuple[LevelContent, ...]]:
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
    return index, tuple(levels)


def assemble(workdir_path: Path) -> AssembleResult:
    """Run stage 5: geometry synthesis, the adventure build, validation, and the artifact writes.

    Args:
        workdir_path: The workdir root; its monsters stage must be `completed`
            and every stage cache present.

    Returns:
        The draft adventure and its extraction report, as written to
        `adventure.json` and `report.json` (plus one preview per level).

    Raises:
        ValueError: If the monsters stage is not `completed`, a cache is
            missing, or the monsters cache is stale against the content caches
            (programmer misuse — `convert`'s ordering makes these unreachable).
    """
    workdir = Workdir(workdir_path)
    run = workdir.read_run()
    monsters_status = run.stages.get(Stage.MONSTERS)
    if monsters_status is None or monsters_status.status != "completed":
        raise ValueError("assemble requires a completed monsters stage")
    index, levels = _load_caches(workdir)
    if not workdir.monsters_json.is_file():
        raise ValueError(f"the monsters cache is missing: {workdir.monsters_json}")
    resolutions = MonsterResolutions.model_validate_json(workdir.monsters_json.read_text(encoding="utf-8"))
    missing = set(encounter_names(levels)) - resolutions.resolutions.keys()
    if missing:
        raise ValueError(f"the monsters cache is stale — unresolved names: {sorted(missing)}; re-run monsters")

    with track_stage(workdir, Stage.GEOMETRY):
        geometries = synthesize_geometry(index, levels)
    with track_stage(workdir, Stage.ASSEMBLE):
        draft = build_draft(index, levels, resolutions, geometries, run.settings)
        validation = _run_validation(draft.adventure)
        resolved_count = sum(1 for resolution in resolutions.resolutions.values() if resolution.template_id is not None)
        usage = TokenUsage()
        for stage in (Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS):
            stage_usage = run.stages[stage].usage
            if stage_usage is not None:
                usage = usage + stage_usage
        report = ExtractionReport(
            module=ModuleInfo(title=index.title, pages=run.page_count),
            validation=validation,
            areas=draft.area_reports,
            monsters=MonsterSummary(resolved=resolved_count, unresolved=draft.unresolved),
            usage=usage,
            flags=draft.module_flags,
        )
        write_json_artifact(
            workdir.adventure_json, stamp_document("adventure", draft.adventure.model_dump(mode="json"))
        )
        write_json_artifact(workdir.report_json, report)
        _write_previews(workdir, draft.adventure)
    return AssembleResult(adventure=draft.adventure, report=report)


def _write_previews(workdir: Workdir, adventure: Adventure) -> None:
    workdir.previews_dir.mkdir(parents=True, exist_ok=True)
    for dungeon in adventure.dungeons:
        for level in dungeon.levels:
            path = workdir.preview_svg(dungeon.id, level.number)
            path.write_text(render_level_svg(dungeon.id, level), encoding="utf-8")


def render_previews(workdir_path: Path) -> tuple[Path, ...]:
    """Regenerate the SVG previews alone — `osrforge preview`.

    Re-runs geometry synthesis over the survey and content caches and rewrites
    `previews/` only, touching neither the other artifacts nor `run.json`.
    The rendered bytes are identical to assembly's — the renderer reads only
    geometry-visible fields.

    Args:
        workdir_path: The workdir root; the survey and content caches must be
            present.

    Returns:
        The written preview paths, in survey order.

    Raises:
        ValueError: If the survey or a level's content cache is missing.
    """
    workdir = Workdir(workdir_path)
    index, levels = _load_caches(workdir)
    geometries = synthesize_geometry(index, levels)
    geometry_by_address = {(geometry.dungeon_id, geometry.level_number): geometry for geometry in geometries}
    workdir.previews_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for dungeon in index.dungeons:
        for survey_level in dungeon.levels:
            geometry = geometry_by_address[(dungeon.id, survey_level.number)]
            level = LevelSpec(
                number=survey_level.number,
                width=geometry.width,
                height=geometry.height,
                edges=geometry.edges,
                areas=tuple(
                    AreaSpec(id=area.key, name=area.name, cells=geometry.areas[area.key]) for area in survey_level.areas
                ),
                transitions=geometry.transitions,
                entrance=geometry.entrance,
            )
            path = workdir.preview_svg(dungeon.id, survey_level.number)
            path.write_text(render_level_svg(dungeon.id, level), encoding="utf-8")
            written.append(path)
    return tuple(written)
