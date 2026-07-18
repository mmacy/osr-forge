"""Overrides application: the human correction channel, applied inside assembly.

Contract models live in `contracts/overrides.py`; this module is assembly-side
application code — the same split as report contracts vs. report production.

The anti-silent-no-op rule, pinned: every entry in a correction file must take
effect or application fails with [`OverrideError`][osrforge.errors.OverrideError]
naming the entry — a typo'd monster name or area address that silently did
nothing would let a human believe a correction landed when it didn't. The
division of labor: **addressing errors are loud; content validity flows to the
report.** An override whose `template_id` doesn't exist in the catalog does
take effect — the dangling id lands in the draft and `validate_adventure`
reports it in `report.json`, which is exactly the loop the human is already in.

Every function here is pure: application is part of assembly, and assembly's
artifacts stay a deterministic function of the stage caches plus the overrides
file.
"""

from dataclasses import dataclass, field

from osrlib.crawl.dungeon import Direction, Edge, Position, TransitionSpec, edge_key

from osrforge.contracts.overrides import AreaOverride, ModuleOverride, Overrides, StatBlockOverride, TownOverride
from osrforge.contracts.report import AreaAddress, LevelAddress
from osrforge.contracts.stages import MonsterResolution, MonsterResolutions, RawStatBlock, SurveyIndex, SurveyLevel
from osrforge.errors import OverrideError
from osrforge.geometry import LevelGeometry, edge_sort_key
from osrforge.monsters import normalize_monster_name

__all__ = [
    "AREA_OVERRIDE_FIELDS",
    "LevelOverridePlan",
    "OverridePlan",
    "apply_level_overrides",
    "apply_monster_overrides",
    "apply_template_overrides",
    "canonicalize_edge_key",
    "effective_roster",
    "plan_overrides",
    "plan_template_overrides",
]

AREA_OVERRIDE_FIELDS = ("name", "description", "encounter", "trap", "treasure", "features")
"""The replaceable per-area fields, in the pinned `overridden` vocabulary order."""


def canonicalize_edge_key(key: str) -> str:
    """Re-key an override edge key through osrlib's own canonical form.

    osrlib stores only `north`/`west` keys — a cell's east edge is its eastern
    neighbour's west edge, its south edge the southern neighbour's north — and
    consults `LevelSpec.edges` through `edge_key`, so a `south`/`east` key
    stored verbatim would never take effect (the phase 0 hazard, defused here):
    the spec's own `"5,2:east"` example becomes `"6,2:west"`.

    Args:
        key: An override edge key, `x,y:direction`, any of the four directions
            (the [`EdgeKeyString`][osrforge.contracts.overrides.EdgeKeyString]
            grammar).

    Returns:
        The canonical edge key.
    """
    coordinates, _, side = key.partition(":")
    x_text, _, y_text = coordinates.partition(",")
    return edge_key((int(x_text), int(y_text)), Direction(side))


def apply_monster_overrides(resolutions: MonsterResolutions, overrides: Overrides) -> MonsterResolutions:
    """Replace cached monster resolutions with the correction file's remaps.

    Override keys match extracted names under the same normalization the
    monsters stage uses, so `"Hobgoblin  Chieftain"` still hits the cache's
    `"hobgoblin chieftain"`. A matched override replaces the cached resolution
    before encounter building: the stand-in (or omission) never happens, no
    `monster_unresolved` flag is emitted, and the report's monsters summary
    counts the name resolved. The result is in-memory only — the monsters
    cache is never rewritten, so a monster correction needs only `assemble`.

    Args:
        resolutions: The monsters-stage cache.
        overrides: The loaded correction file.

    Returns:
        The resolutions with every override applied; the input is unchanged.

    Raises:
        OverrideError: If two override keys normalize to the same name
            (contradictory corrections), or a key matches no name in the cache
            (the error lists the cache's unresolved names — the likeliest
            targets).
    """
    if not overrides.monsters:
        return resolutions
    replaced = dict(resolutions.resolutions)
    claimed: dict[str, str] = {}
    for raw_key, override in overrides.monsters.items():
        name = normalize_monster_name(raw_key)
        if name in claimed:
            raise OverrideError(
                f"monster overrides {claimed[name]!r} and {raw_key!r} both normalize to {name!r} — "
                "contradictory corrections"
            )
        claimed[name] = raw_key
        if name not in replaced:
            unresolved = sorted(
                cached for cached, entry in resolutions.resolutions.items() if entry.template_id is None
            )
            raise OverrideError(
                f"monster override {raw_key!r} matches no extracted name; unresolved names in the cache: {unresolved}"
            )
        replaced[name] = MonsterResolution(template_id=override.template_id, method="override")
    return MonsterResolutions(resolutions=replaced)


def plan_template_overrides(overrides: Overrides, resolutions: MonsterResolutions) -> dict[str, StatBlockOverride]:
    """Resolve and validate the `monster_templates:` entries against the monsters cache.

    Addressing and contradiction errors surface here, before any stage
    tracking or artifact write. The cache-state errors (no `statblocks.json`,
    an `off` knob echo) are assembly's — only assembly knows what caches
    exist — so this function validates what the correction file alone can
    contradict.

    Args:
        overrides: The loaded correction file.
        resolutions: The monsters-stage cache (as written — the extracted-name
            authority the entries address).

    Returns:
        Normalized name → its entry, for every `monster_templates:` entry.

    Raises:
        OverrideError: If two entry keys normalize to the same name, a name
            also appears under `monsters:` ("use this catalog id" and "use
            this custom block" are contradictory corrections), a key matches
            no extracted name (the error lists the cache's unresolved names —
            the likeliest targets), or an entry sets no field beyond `reason`.
    """
    if not overrides.monster_templates:
        return {}
    remapped = {normalize_monster_name(key) for key in overrides.monsters}
    claimed: dict[str, str] = {}
    entries: dict[str, StatBlockOverride] = {}
    for raw_key, entry in overrides.monster_templates.items():
        name = normalize_monster_name(raw_key)
        if name in claimed:
            raise OverrideError(
                f"monster template overrides {claimed[name]!r} and {raw_key!r} both normalize to {name!r} — "
                "contradictory corrections"
            )
        claimed[name] = raw_key
        if name in remapped:
            raise OverrideError(
                f"{name!r} appears under both monsters: and monster_templates: — "
                '"use this catalog id" and "use this custom block" are contradictory corrections'
            )
        if name not in resolutions.resolutions:
            unresolved = sorted(
                cached for cached, cached_entry in resolutions.resolutions.items() if cached_entry.template_id is None
            )
            raise OverrideError(
                f"monster template override {raw_key!r} matches no extracted name; "
                f"unresolved names in the cache: {unresolved}"
            )
        if not (entry.model_fields_set - {"reason"}):
            raise OverrideError(
                f"monster template override {raw_key!r} replaces nothing — set a field or remove the entry"
            )
        entries[name] = entry
    return entries


def apply_template_overrides(
    blocks: dict[str, RawStatBlock | None], entries: dict[str, StatBlockOverride]
) -> dict[str, RawStatBlock | None]:
    """Apply the planned template entries to the cached raw blocks, pre-mapping.

    An entry patches its name's cached block field by field — absent leaves
    the extracted value, explicit `null` clears it back to unprinted — and a
    name with no cached block (or an absent marker) gets a candidate block
    from the entry's fields alone. The result feeds the same refusal ladder
    and mapping an extracted block does; the inputs are unchanged.

    Args:
        blocks: The stat-block cache's `blocks` (normalized name → block or
            absent marker).
        entries: The planned entries
            ([`plan_template_overrides`][osrforge.overrides.plan_template_overrides]).

    Returns:
        The effective candidate blocks.
    """
    result: dict[str, RawStatBlock | None] = dict(blocks)
    for name, entry in entries.items():
        base = result.get(name)
        data = base.model_dump() if base is not None else {}
        for field_name in entry.model_fields_set - {"reason"}:
            value = getattr(entry, field_name)
            if value is None:
                data.pop(field_name, None)
            else:
                data[field_name] = value
        result[name] = RawStatBlock.model_validate(data)
    return result


@dataclass(frozen=True)
class LevelOverridePlan:
    """One survey level's resolved override application, addressing-validated.

    Attributes:
        area_overrides: Survey-area key → its field-replacement entry.
        adds: `(key, entry)` pairs for added areas, in correction-file order.
        removed: Survey-area keys whose `AreaSpec` is skipped (their placed
            cells become corridor — removal deletes content, not floor plan).
        cells: Area key → overridden cell cluster (survey areas and adds).
        edges: The override edge map, canonically keyed.
        entrance_set: Whether the entrance was overridden (a `None` value with
            this set clears it).
        entrance: The overridden entrance.
        transitions_set: Whether the transitions tuple was overridden.
        transitions: The overridden transitions.
    """

    area_overrides: dict[str, AreaOverride] = field(default_factory=dict[str, AreaOverride])
    adds: tuple[tuple[str, AreaOverride], ...] = ()
    removed: frozenset[str] = frozenset()
    cells: dict[str, tuple[Position, ...]] = field(default_factory=dict[str, tuple[Position, ...]])
    edges: dict[str, Edge] = field(default_factory=dict[str, Edge])
    entrance_set: bool = False
    entrance: Position | None = None
    transitions_set: bool = False
    transitions: tuple[TransitionSpec, ...] = ()


@dataclass(frozen=True)
class OverridePlan:
    """The whole correction file, resolved against one survey index.

    Attributes:
        levels: `(dungeon id, level number)` → that level's plan; only levels
            some entry touches appear.
        town: The town entry, validated non-empty.
        module: The module entry, validated non-empty.
    """

    levels: dict[tuple[str, int], LevelOverridePlan] = field(default_factory=dict[tuple[str, int], LevelOverridePlan])
    town: TownOverride | None = None
    module: ModuleOverride | None = None


def _reject_negative(positions: tuple[Position, ...], entry: str) -> None:
    for x, y in positions:
        if x < 0 or y < 0:
            raise OverrideError(f"{entry} carries the negative coordinate ({x}, {y}) — osrlib grids start at (0, 0)")


@dataclass
class _LevelAccumulator:
    area_overrides: dict[str, AreaOverride] = field(default_factory=dict[str, AreaOverride])
    adds: list[tuple[str, AreaOverride]] = field(default_factory=list[tuple[str, AreaOverride]])
    removed: set[str] = field(default_factory=set[str])
    cells: dict[str, tuple[Position, ...]] = field(default_factory=dict[str, tuple[Position, ...]])
    edges: dict[str, Edge] = field(default_factory=dict[str, Edge])
    entrance_set: bool = False
    entrance: Position | None = None
    transitions_set: bool = False
    transitions: tuple[TransitionSpec, ...] = ()


def plan_overrides(index: SurveyIndex, overrides: Overrides) -> OverridePlan:
    """Resolve and validate every non-monster override entry against the survey.

    All addressing and contradiction errors surface here, before any stage
    tracking or artifact write — a correction file that cannot take effect
    fails the command without touching the workdir. Monster overrides validate
    separately (they address the monsters cache, not the survey) in
    [`apply_monster_overrides`][osrforge.overrides.apply_monster_overrides].

    Args:
        index: The survey cache the entries address.
        overrides: The loaded correction file.

    Returns:
        The resolved plan.

    Raises:
        OverrideError: If an entry addresses no surveyed level or area and is
            not a well-formed add (an add needs `name`, `description`, and a
            geometry override supplying its cells), contradicts itself
            (`remove` plus replacement fields or a cells entry), replaces
            nothing, carries a negative coordinate, or two edge keys
            canonicalize to the same edge.
    """
    survey_keys = {
        (dungeon.id, level.number): frozenset(area.key for area in level.areas)
        for dungeon in index.dungeons
        for level in dungeon.levels
    }
    accumulators: dict[tuple[str, int], _LevelAccumulator] = {}

    def accumulator(level_key: tuple[str, int]) -> _LevelAccumulator:
        return accumulators.setdefault(level_key, _LevelAccumulator())

    for address_text, area_override in overrides.areas.items():
        address = AreaAddress.parse(address_text)
        level_key = (address.dungeon_id, address.level_number)
        keys = survey_keys.get(level_key)
        if keys is None:
            raise OverrideError(f"area override {address_text!r} addresses no surveyed level")
        entry = f"area override {address_text!r}"
        set_fields = [name for name in AREA_OVERRIDE_FIELDS if name in area_override.model_fields_set]
        level_address = LevelAddress(dungeon_id=address.dungeon_id, level_number=address.level_number)
        geometry_override = overrides.geometry.get(str(level_address))
        has_cells = geometry_override is not None and address.area_key in geometry_override.areas
        if address.area_key not in keys:
            # An entry addressing an area the survey doesn't have is an add.
            if area_override.remove:
                raise OverrideError(f"{entry} removes an area the survey doesn't have")
            payload_complete = (
                "name" in area_override.model_fields_set
                and area_override.name is not None
                and "description" in area_override.model_fields_set
                and area_override.description is not None
            )
            if not payload_complete or not has_cells:
                raise OverrideError(
                    f"{entry} addresses no surveyed area — an add must carry name, description, "
                    "and a geometry override supplying its cells"
                )
            accumulator(level_key).adds.append((address.area_key, area_override))
            continue
        if area_override.remove:
            if set_fields:
                raise OverrideError(f"{entry} combines remove with replacement fields {set_fields}")
            if has_cells:
                raise OverrideError(f"{entry} combines remove with a geometry cells entry for the same area")
            accumulator(level_key).removed.add(address.area_key)
            continue
        if not set_fields:
            raise OverrideError(f"{entry} replaces nothing — set a field or remove the entry")
        accumulator(level_key).area_overrides[address.area_key] = area_override

    for address_text, geometry_override in overrides.geometry.items():
        address = LevelAddress.parse(address_text)
        level_key = (address.dungeon_id, address.level_number)
        keys = survey_keys.get(level_key)
        if keys is None:
            raise OverrideError(
                f"geometry override {address_text!r} addresses no surveyed level — "
                "a whole missing level is a re-extraction problem, not a correction"
            )
        entry = f"geometry override {address_text!r}"
        added_keys = {key for key, _ in accumulator(level_key).adds}
        takes_effect = False
        for area_key, area_geometry in geometry_override.areas.items():
            if area_key not in keys and area_key not in added_keys:
                raise OverrideError(f"{entry} places cells for unknown area {area_key!r}")
            _reject_negative(area_geometry.cells, f"{entry} area {area_key!r}")
            accumulator(level_key).cells[area_key] = area_geometry.cells
            takes_effect = True
        sources: dict[str, str] = {}
        for raw_key, edge in geometry_override.edges.items():
            canonical = canonicalize_edge_key(raw_key)
            if canonical in sources:
                raise OverrideError(
                    f"{entry} edges {sources[canonical]!r} and {raw_key!r} canonicalize to the same edge {canonical!r}"
                )
            sources[canonical] = raw_key
            accumulator(level_key).edges[canonical] = edge
            takes_effect = True
        if "entrance" in geometry_override.model_fields_set:
            if geometry_override.entrance is not None:
                _reject_negative((geometry_override.entrance,), f"{entry} entrance")
            accumulator(level_key).entrance_set = True
            accumulator(level_key).entrance = geometry_override.entrance
            takes_effect = True
        if "transitions" in geometry_override.model_fields_set:
            transitions = geometry_override.transitions if geometry_override.transitions is not None else ()
            for transition in transitions:
                _reject_negative((transition.position, transition.to_position), f"{entry} transitions")
            accumulator(level_key).transitions_set = True
            accumulator(level_key).transitions = transitions
            takes_effect = True
        if not takes_effect:
            raise OverrideError(f"{entry} replaces nothing — set a field or remove the entry")

    for kind, entry_override in (("town", overrides.town), ("module", overrides.module)):
        if entry_override is not None and not (entry_override.model_fields_set - {"reason"}):
            raise OverrideError(f"the {kind} override replaces no fields — set a field or remove the entry")

    levels = {
        level_key: LevelOverridePlan(
            area_overrides=acc.area_overrides,
            adds=tuple(acc.adds),
            removed=frozenset(acc.removed),
            cells=acc.cells,
            edges=acc.edges,
            entrance_set=acc.entrance_set,
            entrance=acc.entrance,
            transitions_set=acc.transitions_set,
            transitions=acc.transitions,
        )
        for level_key, acc in accumulators.items()
    }
    return OverridePlan(levels=levels, town=overrides.town, module=overrides.module)


def apply_level_overrides(geometry: LevelGeometry, plan: LevelOverridePlan | None) -> LevelGeometry:
    """Apply one level's plan to its synthesized geometry, before `LevelSpec` construction.

    Overridden cells replace an area's cluster wholesale and are kept in the
    author's order (the human's word verbatim); a removed area's placed cells
    become corridor; added areas append after the survey areas. Override edges
    merge over the synthesized map, override winning per canonical key (a
    `wall` entry legitimately seals a synthesized opening), and the merged map
    is re-sorted under the pinned key order for byte stability. `width` and
    `height` are recomputed as the bounding box of the final area and corridor
    cells. Synthesis postconditions are **not** re-asserted — human input is a
    runtime condition, not a bug; what a bad geometry override breaks,
    `validate_adventure` and the playability lint report.

    Args:
        geometry: The synthesized level geometry.
        plan: The level's plan, or `None` when nothing touches the level.

    Returns:
        The effective geometry; extraction-side facts (connection ambiguities,
        disconnected components) carry through unchanged — no override kind
        touches the extracted connection graph.
    """
    if plan is None:
        return geometry
    areas: dict[str, tuple[Position, ...]] = {}
    freed: list[Position] = []
    for key, cells in geometry.areas.items():
        if key in plan.removed:
            freed.extend(cells)
            continue
        areas[key] = plan.cells.get(key, cells)
    for key, _ in plan.adds:
        areas[key] = plan.cells[key]
    corridors = tuple(sorted(set(geometry.corridors) | set(freed), key=lambda cell: (cell[1], cell[0])))
    edges = dict(sorted({**geometry.edges, **plan.edges}.items(), key=lambda item: edge_sort_key(item[0])))
    entrance = plan.entrance if plan.entrance_set else geometry.entrance
    transitions = plan.transitions if plan.transitions_set else geometry.transitions
    all_cells = {cell for cells in areas.values() for cell in cells} | set(corridors)
    return LevelGeometry(
        dungeon_id=geometry.dungeon_id,
        level_number=geometry.level_number,
        width=max((x for x, _ in all_cells), default=0) + 1,
        height=max((y for _, y in all_cells), default=0) + 1,
        areas=areas,
        corridors=corridors,
        edges=edges,
        entrance=entrance,
        transitions=transitions,
        unresolved_connections=geometry.unresolved_connections,
        unknown_direction_connections=geometry.unknown_direction_connections,
        disconnected_areas=geometry.disconnected_areas,
        # Overridden transitions replace the guessed landings wholesale, so
        # their review badges drop with them — the same rule that drops
        # `geometry_synthesized` on overridden cells; connection facts persist.
        guessed_transitions=() if plan.transitions_set else geometry.guessed_transitions,
    )


def effective_roster(survey_level: SurveyLevel, plan: LevelOverridePlan | None) -> tuple[tuple[str, str], ...]:
    """The draft's ordered area roster for one level: `(key, name)` pairs.

    Survey areas in survey order minus removals, then adds in correction-file
    order — the one ordering rule `build_draft` and `render_previews` must
    agree on, so previews always show the corrected roster.

    Args:
        survey_level: The survey level.
        plan: The level's plan, or `None`.

    Returns:
        The ordered roster. Names are the survey's for survey areas and the
        override's for adds (name overrides are content-side and don't affect
        what the preview labels — it renders keys, not names).
    """
    entries = [(area.key, area.name) for area in survey_level.areas if plan is None or area.key not in plan.removed]
    if plan is not None:
        entries.extend((key, override.name or "") for key, override in plan.adds)
    return tuple(entries)
