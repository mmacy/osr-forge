"""Stage 5: assembly — overrides application, the `Adventure` build, validation, report production, artifact writing.

Assembly is pure: `adventure.json`, `report.json`, and the previews are a
deterministic function of the cached stage outputs *plus `overrides.yaml`* —
[assembly purity][assembly-purity], the core guarantee. `overrides.yaml` is loaded once and
threaded through; every override addressing error surfaces before any stage
tracking or artifact write, so a correction file that cannot take effect fails
the command without touching the workdir.

Two sequential trackings: `geometry` around synthesis and geometry-override
application, then `assemble` around the build, validation, and artifact writes
— so a failure in the second leaves an honest `geometry: completed`. Neither
stage touches a provider; usage stays zero and `run.json`'s provider identity
is untouched.

Flags describe the built draft — the unifying rule: because overrides replace
inputs to the build, every flag falls out of the path actually taken (an
overridden encounter emits no monster or count flags; overridden cells drop
`geometry_synthesized`; an overridden title emits no default flag), while
extraction facts — `confidence`, `source_pages`, connection flags — persist
regardless.
"""

import hashlib
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from osrlib.core.alignment import Alignment
from osrlib.core.classes import SavingThrows
from osrlib.core.dice import parse as parse_dice
from osrlib.core.items import Coins
from osrlib.core.monsters import (
    AlignmentSpec,
    AttackRoutine,
    MonsterAbility,
    MonsterAttack,
    MonsterHitDice,
    MonsterSaves,
    MonsterTemplate,
    MovementMode,
    NumberAppearing,
    NumberAppearingValue,
    TreasureRef,
)
from osrlib.core.tables import EncounterTable, MonsterEncounterEntry, monster_save_band_label, monster_xp, thac0_for_hd
from osrlib.crawl.adventure import Adventure, TownSpec, validate_adventure
from osrlib.crawl.dungeon import (
    AreaSpec,
    AreaTreasureSpec,
    DungeonSpec,
    FeatureSpec,
    KeyedEncounter,
    KeyedMonster,
    LevelSpec,
    Position,
    TrapEffect,
    TrapSpec,
    ValuableSpec,
)
from osrlib.data import load_classes, load_combat_tables, load_encounter_tables, load_equipment, load_monsters
from osrlib.errors import ContentValidationError
from osrlib.versioning import stamp_document

from osrforge.contracts.overrides import AreaOverride, ModuleOverride, TownOverride, load_overrides
from osrforge.contracts.report import (
    AreaReport,
    CustomMonsterRecord,
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
    MonsterResolution,
    MonsterResolutions,
    RawStatBlock,
    StatBlocks,
    SurveyIndex,
)
from osrforge.errors import OverrideError
from osrforge.geometry import LevelGeometry, synthesize_geometry
from osrforge.monsters import encounter_names, normalize_monster_name
from osrforge.overrides import (
    AREA_OVERRIDE_FIELDS,
    OverridePlan,
    apply_level_overrides,
    apply_monster_overrides,
    apply_template_overrides,
    effective_roster,
    plan_overrides,
    plan_template_overrides,
)
from osrforge.previews import render_level_svg
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir, track_stage, write_json_artifact

__all__ = [
    "AssembleResult",
    "EmittedTemplate",
    "assemble",
    "build_draft",
    "emit_custom_templates",
    "map_stat_block",
    "parse_treasure",
    "render_previews",
    "usable_stat_block",
]

# The unanchored dice scan (osrlib's die sizes, optional count): a treasure
# string containing dice notation is per-monster or conditional treasure and
# cannot be a fixed cache.
_DICE_SCAN = re.compile(r"\b(?:[1-9][0-9]{0,2})?d(?:2|3|4|6|8|10|12|20|100)\b")
_EACH_PER = re.compile(r"\b(?:each|per)\b", re.IGNORECASE)
# Numbers accept comma thousands-separators ("1,000 cp") — strict groups of
# three, so a mis-grouped number never half-matches.
_NUMBER = r"[1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*"
_WORTH = re.compile(rf"^(?P<name>.+?)\s+worth\s+(?P<value>{_NUMBER})\s*gp\.?$", re.IGNORECASE)
# The two quantified-each orderings: "3 gems worth 50 gp each" and
# "3 gems each worth 50 gp" — a literal count distributing a stated value.
_WORTH_EACH = re.compile(
    rf"^(?P<count>{_NUMBER})\s+(?P<name>.+?)\s+worth\s+(?P<value>{_NUMBER})\s*gp\s+each\.?$", re.IGNORECASE
)
_EACH_WORTH = re.compile(
    rf"^(?P<count>{_NUMBER})\s+(?P<name>.+?)\s+each\s+worth\s+(?P<value>{_NUMBER})\s*gp\.?$", re.IGNORECASE
)
_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)
_GEM_WORD = re.compile(r"\bgems?\b", re.IGNORECASE)
_COIN = re.compile(rf"((?:{_NUMBER}))\s*(cp|sp|ep|gp|pp)\b", re.IGNORECASE)
_TREASURE_TYPE = re.compile(r"^treasure types?\s+([A-Va-v])\.?$", re.IGNORECASE)


def _number(text: str) -> int:
    return int(text.replace(",", ""))


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

    Per string, tried in order, first match wins: dice notation → unparsed (a
    dice quantity is per-monster or conditional treasure and cannot be a fixed
    cache); the two quantified-`each` shapes — `<N> <things> worth <V> gp
    each` and `<N> <things> each worth <V> gp` — → N valuables of the stated
    value; any other `each` and every `per` → unparsed (still conditional
    treasure); `<thing> worth <N> gp` → a valuable (`gem` exactly when the
    thing names a gem, else `jewellery` — both kinds carry identical value and
    XP semantics, so the narrow lexicon errs harmlessly); money references
    `<N> <cp|sp|ep|gp|pp>` with no digits outside them → coins summed per
    denomination; `treasure type <A-V>` → a generated-treasure letter.
    Numbers accept comma thousands-separators (`1,000 cp`), and a
    comma-grouped number counts as one coin match under the no-stray-digits
    guard. Anything else is unparsed — assembly flags it and (under
    `best-effort`) compensates with an unguarded-treasure roll. A string that
    is empty after stripping is skipped outright: it carries no information to
    flag, and the [frozen stage-cache schema][frozen-schema] does not forbid
    it, so it must not crash assembly.

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
        if _DICE_SCAN.search(text):
            unparsed.append(raw)
            continue
        quantified = _WORTH_EACH.match(text) or _EACH_WORTH.match(text)
        if quantified is not None:
            name = quantified.group("name").strip()
            kind = "gem" if _GEM_WORD.search(name) else "jewellery"
            value = _number(quantified.group("value"))
            valuables.extend(
                ValuableSpec(kind=kind, name=name, value_gp=value) for _ in range(_number(quantified.group("count")))
            )
            continue
        if _EACH_PER.search(text):
            unparsed.append(raw)
            continue
        worth = _WORTH.match(text)
        if worth is not None:
            name = _ARTICLE.sub("", worth.group("name")).strip()
            kind = "gem" if _GEM_WORD.search(name) else "jewellery"
            valuables.append(ValuableSpec(kind=kind, name=name, value_gp=_number(worth.group("value"))))
            continue
        coin_matches = _COIN.findall(text)
        if coin_matches and not any(character.isdigit() for character in _COIN.sub("", text)):
            for amount, denomination in coin_matches:
                coins_by_denomination[denomination.lower()] += _number(amount)
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


# ---------------------------------------------------------------------------
# Custom-template mapping: deterministic assembly of cached raw stat
# blocks into MonsterTemplates. Every rules judgment lives here — the
# stat-block pass only transcribes — under one non-negotiable rule: a mapped
# value is either traceably printed or recorded as derived.
# ---------------------------------------------------------------------------

_AC_DUAL = re.compile(r"^\s*(-?\d+)\s*\[\s*(-?\d+)\s*\]\s*$")
_FIRST_INT = re.compile(r"-?\d+")
_HD_FRACTION = re.compile(r"½|¼|\b1\s*/\s*[248]\b")
_HD_MAIN = re.compile(r"(\d+)\s*(?:d\s*(\d+))?\s*([+-]\s*\d+)?")
_CLASS_LETTER = re.compile(r"^\s*(mu|[fcmtdeh])\W*(\d+)\s*$", re.IGNORECASE)
_CLASS_WORDS: tuple[tuple[str, str], ...] = (
    ("magic-user", "magic_user"),
    ("magic user", "magic_user"),
    ("magicuser", "magic_user"),
    ("fighter", "fighter"),
    ("cleric", "cleric"),
    ("thief", "thief"),
    ("dwarf", "dwarf"),
    ("elf", "elf"),
    ("halfling", "halfling"),
)
_CLASS_LETTER_IDS = {
    "f": "fighter",
    "c": "cleric",
    "m": "magic_user",
    "mu": "magic_user",
    "t": "thief",
    "d": "dwarf",
    "e": "elf",
    "h": "halfling",
}
_SAVE_AS_LETTERS = {
    "fighter": "F",
    "cleric": "C",
    "magic_user": "MU",
    "thief": "T",
    "dwarf": "D",
    "elf": "E",
    "halfling": "H",
}
_THAC0_BRACKET = re.compile(r"\[\s*([+-]?\d+)\s*\]")
_THAC0_LEAD = re.compile(r"^\s*(\d+)")
_THAC0_BONUS = re.compile(r"(?<![\d\[])([+-]\d+)")
_SAVE_LABEL = re.compile(r"^\s*(?:sv\.?|saves?)\s*:?\s*", re.IGNORECASE)
_SAVES_DWPBS = re.compile(r"d\s*(\d+)\W+w\s*(\d+)\W+p\s*(\d+)\W+b\s*(\d+)\W+s\s*(\d+)", re.IGNORECASE)
_PAREN = re.compile(r"\(([^)]*)\)")
_SAVE_AS = re.compile(r"\bsaves?\s+as\s+(.+)$", re.IGNORECASE)
_DICE_TOKEN = re.compile(r"(\d*)\s*d\s*(\d+)\s*((?:[+-]\s*\d+)?)")
_RANGE_TOKEN = re.compile(r"(\d+)\s*[-–]\s*(\d+)")  # noqa: RUF001 — modules print en-dash ranges
_MOVE_TOKEN = re.compile(
    r"(?:([A-Za-z][A-Za-z ]*?)[\s:]+)?(\d+)\s*(?:'|ft\.?|feet)?\s*(?:\(\s*(\d+)\s*(?:'|ft\.?|feet)?\s*\))?"
)
_MOVE_LABELS = frozenset({"mv", "move", "movement", "spd", "speed"})
_ATTACK_ITEM = re.compile(r"(?:(\d+)\s*[x×]?\s+)?([A-Za-z][A-Za-z' \-]*?)\s*\(([^)]*)\)")  # noqa: RUF001
# The labelled BFRPG form: "1 bite, Dam 1d8", "6 tentacles, Dam paralysis", bare "1 spear".
_ATTACK_DAM = re.compile(
    r"^(?:(\d+)\s*[x×]?\s+)?([A-Za-z][A-Za-z' ,;\-]*?)(?:[,;:]?\s+dam(?:age)?\.?:?\s+(.+))?$",  # noqa: RUF001
    re.IGNORECASE,
)
# Effect keywords the damage clause can print, mapped to osrlib's tag spellings.
_EFFECT_WORDS: tuple[tuple[str, str], ...] = (
    ("paraly", "paralysis"),
    ("poison", "poison"),
    ("energy drain", "energy_drain"),
    ("petrif", "petrification"),
    ("charm", "charm"),
    ("disease", "disease"),
)
# " or " outside parentheses separates alternative routines; inside them it is
# damage prose ("1d6 or paralysis") — the lookahead holds for the unnested
# parens stat blocks print.
_ROUTINE_SPLIT = re.compile(r"\s+or\s+(?![^()]*\))", re.IGNORECASE)
_DIE_SIZES = frozenset({2, 3, 4, 6, 8, 10, 12, 20, 100})


@dataclass(frozen=True)
class _ParsedHd:
    """A printed Hit Dice line, structurally parsed: count, printed die (if any), modifier, asterisks."""

    count: int
    die: int | None
    modifier: int
    asterisks: int
    fractional: bool


@dataclass(frozen=True)
class EmittedTemplate:
    """One emitted custom template with its review record inputs."""

    template: MonsterTemplate
    derived: tuple[str, ...]
    source_pages: tuple[int, ...]


def _parse_ac(block: RawStatBlock) -> tuple[int, int, bool] | None:
    """Parse the printed AC into `(descending, ascending, complement_derived)`, or None.

    Dual notation carries both values as printed; a single value converts by
    the 19-complement (the B/X identity OSE prints directly: `AC 5 [14]`) in
    the direction the block's notation states, defaulting to descending — the
    B/X reading — when the notation is unclassified.
    """
    if block.ac is None:
        return None
    dual = _AC_DUAL.match(block.ac)
    if dual is not None:
        return int(dual.group(1)), int(dual.group(2)), False
    match = _FIRST_INT.search(block.ac)
    if match is None:
        return None
    value = int(match.group())
    if block.ac_notation == "ascending":
        return 19 - value, value, True
    return value, 19 - value, True


def _parse_hd_text(text: str | None) -> _ParsedHd | None:
    """Structurally parse a printed HD line (`3+1`, `1-1`, `3*`, `½`, `2d8`), or None."""
    if text is None or not text.strip():
        return None
    stripped = text.strip()
    asterisks = stripped.count("*")
    if _HD_FRACTION.search(stripped):
        return _ParsedHd(count=0, die=None, modifier=0, asterisks=asterisks, fractional=True)
    match = _HD_MAIN.search(stripped)
    if match is None:
        return None
    die = int(match.group(2)) if match.group(2) else None
    modifier = int(match.group(3).replace(" ", "")) if match.group(3) else 0
    return _ParsedHd(count=int(match.group(1)), die=die, modifier=modifier, asterisks=asterisks, fractional=False)


def _parse_class_level(text: str | None) -> tuple[str, int] | None:
    """Parse a printed class-level notation (`F 3`, `MU4`, `"3rd-level cleric"`) into `(class_id, level)`.

    A level below 1 refuses in both forms — a 0-level notation carries no
    combat math to derive, so it must fall to the refusal ladder, never into
    mapping (which is total only over parses this function accepts).
    """
    if text is None:
        return None
    lowered = text.casefold()
    letter = _CLASS_LETTER.match(lowered)
    if letter is not None:
        level = int(letter.group(2))
        return (_CLASS_LETTER_IDS[letter.group(1)], level) if level >= 1 else None
    for word, class_id in _CLASS_WORDS:
        if word in lowered:
            numbers = re.findall(r"\d+", lowered)
            if numbers and int(numbers[0]) >= 1:
                return class_id, int(numbers[0])
            return None
    return None


def usable_stat_block(block: RawStatBlock | None) -> bool:
    """The refusal ladder's eligibility predicate: an AC plus an HD line or a class-level notation.

    A block failing this refuses emission — there is nothing to derive combat
    math from, and emit-with-invented-combat-math would be invention. Shared
    verbatim with the eval scorer's custom-assertion match signal, so the
    metric can never score an emission assembly would refuse.

    Args:
        block: A cached raw block, or the absent marker.

    Returns:
        Whether mapping would emit a template from this block.
    """
    if block is None:
        return False
    if _parse_ac(block) is None:
        return False
    return _parse_hd_text(block.hit_dice) is not None or _parse_class_level(block.class_level) is not None


def _class_row(class_id: str, level: int):
    rows = load_classes().get(class_id).progression
    clamped = min(level, rows[-1].level)
    for row in rows:
        if row.level == clamped:
            return row
    raise AssertionError(f"class {class_id} has no level {clamped} row")  # pragma: no cover — progressions are dense


def _band_saves(hit_dice: MonsterHitDice) -> tuple[SavingThrows, str]:
    label = monster_save_band_label(hit_dice)
    for band in load_combat_tables().monster_saves:
        if band.label == label:
            return band.saves, label
    raise AssertionError(f"no monster save band labelled {label!r}")  # pragma: no cover — the bands are total


def _map_hit_dice(parsed: _ParsedHd, hp: int | None, special_count: int) -> tuple[MonsterHitDice, list[str]]:
    """Map the parsed HD onto `MonsterHitDice` under the pinned anchors.

    Die 8 unless the block prints d4; a printed die the model can't carry
    keeps the count on die 8 with printed hp as `fixed_hp`; fractional HD
    maps to count 0 with `fixed_hp` = printed hp, else 3. Asterisks are the
    printed count when printed, else one per extracted special-ability line
    (the B/X XP rule's input).
    """
    derived: list[str] = []
    asterisks = parsed.asterisks if parsed.asterisks else special_count
    if parsed.fractional or parsed.count < 1:
        fixed = hp if hp is not None else 3
        if hp is None:
            derived.append("fixed_hp")
        return MonsterHitDice(count=0, modifier=parsed.modifier, asterisks=asterisks, fixed_hp=fixed), derived
    if parsed.die is not None and parsed.die not in (4, 8):
        derived.append("hit_dice")
        return (
            MonsterHitDice(count=parsed.count, modifier=parsed.modifier, asterisks=asterisks, fixed_hp=hp),
            derived,
        )
    die = parsed.die if parsed.die is not None else 8
    return (
        MonsterHitDice(count=parsed.count, die=die, modifier=parsed.modifier, asterisks=asterisks, average_hp=hp),
        derived,
    )


def _parse_thac0(text: str | None) -> tuple[int, int] | None:
    """Parse a printed to-hit line (`17`, `19 [+0]`, `+2`, `AB +1`) into `(thac0, attack_bonus)`.

    The pair always satisfies osrlib's own identity `attack_bonus = 19 - thac0`,
    with the descending value clamped to the model's 2..20 band. A labelled
    bonus form (`AB +1`, the BFRPG print) parses by its signed number.
    """
    if text is None:
        return None
    lead = _THAC0_LEAD.match(text)
    if lead is not None:
        thac0 = int(lead.group(1))
    else:
        bonus = _THAC0_BRACKET.search(text) or _THAC0_BONUS.search(text)
        if bonus is None:
            return None
        thac0 = 19 - int(bonus.group(1))
    thac0 = max(2, min(20, thac0))
    return thac0, 19 - thac0


def _map_saves(
    text: str | None, hit_dice: MonsterHitDice, class_level: tuple[str, int] | None
) -> tuple[MonsterSaves, list[str]]:
    """Map the printed saves line under the pinned anchors.

    Printed D/W/P/B/S values win (save-as from the printed parenthetical, else
    the derived band label); a printed `save as <class><level>` — including
    the labelled and bare BFRPG forms `Sv F2` and `F3` — uses the class
    table; a class-leveled block with no parseable saves line saves as its
    printed class level; Fort/Ref/Will and everything else discard and derive
    from HD via the monster save bands.
    """
    derived: list[str] = []
    if text is not None:
        dwpbs = _SAVES_DWPBS.search(text)
        if dwpbs is not None:
            raw = [int(dwpbs.group(index)) for index in range(1, 6)]
            clamped = [max(2, min(20, value)) for value in raw]
            if clamped != raw:
                derived.append("saves")
            values = SavingThrows(
                death=clamped[0], wands=clamped[1], paralysis=clamped[2], breath=clamped[3], spells=clamped[4]
            )
            paren = _PAREN.search(text)
            if paren is not None and paren.group(1).strip():
                save_as = paren.group(1).strip()
            else:
                save_as = monster_save_band_label(hit_dice)
                derived.append("save_as")
            return MonsterSaves(values=values, save_as=save_as), derived
        save_as_match = _SAVE_AS.search(text)
        if save_as_match is not None:
            parsed = _parse_class_level(save_as_match.group(1))
            if parsed is not None:
                class_id, level = parsed
                row = _class_row(class_id, level)
                return MonsterSaves(values=row.saves, save_as=f"{_SAVE_AS_LETTERS[class_id]}{level}"), derived
        # The labelled and bare BFRPG forms: "Sv F2", "Saves: F3", "F3".
        bare = _parse_class_level(_SAVE_LABEL.sub("", text))
        if bare is not None:
            class_id, level = bare
            row = _class_row(class_id, level)
            return MonsterSaves(values=row.saves, save_as=f"{_SAVE_AS_LETTERS[class_id]}{level}"), derived
    if class_level is not None:
        class_id, level = class_level
        row = _class_row(class_id, level)
        derived.append("saves")
        return MonsterSaves(values=row.saves, save_as=f"{_SAVE_AS_LETTERS[class_id]}{level}"), derived
    values, label = _band_saves(hit_dice)
    derived.append("saves")
    return MonsterSaves(values=values, save_as=label), derived


def _range_to_dice(low: int, high: int) -> str | None:
    """Convert printed range notation to dice: `1-10` → `1d10`, `2-8` → `2d4`, `2-7` → `1d6+1`.

    The multi-dice reading first (`N-M` as N dice of M/N — the printed-count
    form), then the exact uniform form (`1d(span)+(low-1)`, distribution-
    identical to the printed range). Both are conversions of the printed
    value, not derivations; a range neither rule fits stays unconverted and
    the caller records the miss.
    """
    if low < 1 or high <= low:
        return None
    if high % low == 0 and (high // low) in _DIE_SIZES:
        return f"{low}d{high // low}"
    span = high - low + 1
    if span in _DIE_SIZES:
        modifier = f"+{low - 1}" if low > 1 else ""
        return f"1d{span}{modifier}"
    return None


def _normalized_dice(text: str) -> str | None:
    """The first dice token in `text`, normalized and validated against osrlib's grammar."""
    match = _DICE_TOKEN.search(text)
    if match is None:
        return None
    candidate = f"{match.group(1) or '1'}d{match.group(2)}{match.group(3).replace(' ', '')}"
    try:
        parse_dice(candidate)
    except ContentValidationError:
        return None
    return candidate


def _parse_na_value(text: str) -> NumberAppearingValue | None:
    dice = _normalized_dice(text)
    if dice is not None:
        return NumberAppearingValue(dice=dice)
    range_match = _RANGE_TOKEN.search(text)
    if range_match is not None:
        converted = _range_to_dice(int(range_match.group(1)), int(range_match.group(2)))
        return NumberAppearingValue(dice=converted) if converted is not None else None
    fixed = re.search(r"\d+", text)
    if fixed is not None and int(fixed.group()) >= 1:
        return NumberAppearingValue(fixed=int(fixed.group()))
    return None


def _parse_number_appearing(text: str | None) -> NumberAppearing | None:
    """Parse a printed NA line: `1d6 (2d6)` fills dungeon then lair; a lone value fills both."""
    if text is None:
        return None
    paren = _PAREN.search(text)
    first = _parse_na_value(text[: paren.start()] if paren is not None else text)
    if first is None:
        return None
    second = _parse_na_value(paren.group(1)) if paren is not None else None
    return NumberAppearing(dungeon=first, lair=second if second is not None else first)


def _encounter_rate(rate: int) -> int:
    """One third of the turn rate, rounded to the nearest 10', floor 10'."""
    return max(10, int(rate / 30 + 0.5) * 10)


def _map_movement(text: str | None) -> tuple[tuple[MovementMode, ...], list[str]]:
    """Map the printed movement line onto `MovementMode`s.

    A printed pair (`120' (40')`) is used verbatim; a lone printed rate is the
    turn rate with the encounter rate derived by thirds; modes split on commas
    and slashes with any leading word kept as the descriptor (movement-line
    labels like `MV` excluded); no movement at all defaults to `120' (40')`,
    the B/X human norm.
    """
    derived: list[str] = []
    modes: list[MovementMode] = []
    if text is not None:
        for chunk in re.split(r"[,/]", text):
            match = _MOVE_TOKEN.search(chunk)
            if match is None:
                continue
            descriptor = match.group(1).strip().casefold() if match.group(1) else None
            if descriptor in _MOVE_LABELS:
                descriptor = None
            rate = int(match.group(2))
            if match.group(3) is not None:
                encounter_rate = int(match.group(3))
            else:
                encounter_rate = _encounter_rate(rate)
                derived.append("movement")
            modes.append(MovementMode(rate_feet=rate, encounter_rate_feet=encounter_rate, descriptor=descriptor))
    if not modes:
        return (MovementMode(rate_feet=120, encounter_rate_feet=40),), ["movement"]
    return tuple(modes), derived


def _attack_effects(damage_text: str) -> tuple[str, ...]:
    """The printed effect keywords in a damage clause (`1d6 + poison`, `Dam paralysis`)."""
    lowered = damage_text.casefold()
    effects: list[str] = []
    for token, effect in _EFFECT_WORDS:
        if token in lowered and effect not in effects:
            effects.append(effect)
    return tuple(effects)


def _parse_attack(count_text: str | None, name: str, damage_text: str | None) -> tuple[MonsterAttack, bool]:
    """Parse one attack item; the second value is whether the printed damage carried over faithfully.

    A range-shaped damage clause neither conversion rule fits maps with no
    damage and reports unfaithful — falling through to the flat-integer path
    would silently deal the range's low end, a guess the mapping rule bans.
    """
    count = int(count_text) if count_text else 1
    if damage_text is None:
        return MonsterAttack(count=count, name=name), False
    effects = _attack_effects(damage_text)
    dice = _normalized_dice(damage_text)
    range_match = _RANGE_TOKEN.search(damage_text) if dice is None else None
    if dice is None and range_match is not None:
        dice = _range_to_dice(int(range_match.group(1)), int(range_match.group(2)))
        if dice is None:
            return MonsterAttack(count=count, name=name, effects=effects), False
    if dice is not None:
        return MonsterAttack(count=count, name=name, damage=dice, effects=effects), True
    if "weapon" in damage_text.casefold():
        return MonsterAttack(count=count, name=name, by_weapon=True, effects=effects), True
    fixed = re.search(r"\d+", damage_text)
    if fixed is not None and int(fixed.group()) >= 1:
        return MonsterAttack(count=count, name=name, fixed_damage=int(fixed.group()), effects=effects), True
    return MonsterAttack(count=count, name=name, effects=effects), bool(effects)


def _segment_attacks(segment: str) -> tuple[list[MonsterAttack], bool]:
    """Parse one routine segment; returns the attacks and whether every printed damage carried faithfully.

    Two grammars, tried in order: the parenthesized form (`2 claws (1d4)`,
    the OSE print) and the labelled form (`1 bite, Dam 1d8` / bare `1 spear`,
    the BFRPG print — damage after a `Dam` label, or none printed at all).
    """
    parenthesized = [
        _parse_attack(match.group(1), match.group(2).strip(), match.group(3))
        for match in _ATTACK_ITEM.finditer(segment)
    ]
    if parenthesized:
        return [attack for attack, _ in parenthesized], all(faithful for _, faithful in parenthesized)
    labelled = _ATTACK_DAM.match(segment.strip())
    if labelled is not None and labelled.group(2).strip() and (labelled.group(1) or labelled.group(3)):
        # A printed count or a damage clause anchors the form; free prose
        # ("see below") matches neither and stays unparsed.
        attack, faithful = _parse_attack(labelled.group(1), labelled.group(2).strip(" ,;"), labelled.group(3))
        return [attack], faithful
    return [], False


def _map_attacks(lines: Sequence[str]) -> tuple[tuple[AttackRoutine, ...], list[str]]:
    """Parse printed attack lines into routines: each line's ` or `-separated segments are alternatives.

    A segment with no parseable attack is dropped and the drop recorded; an
    attack whose printed damage could not carry over faithfully (no damage
    clause, or a range no conversion rule fits) is recorded the same way; a
    block with no parseable attack at all maps with `attacks=()` — flagged,
    never guessed.
    """
    routines: list[AttackRoutine] = []
    incomplete = False
    for line in lines:
        for segment in _ROUTINE_SPLIT.split(line):
            attacks, faithful = _segment_attacks(segment)
            if attacks:
                routines.append(AttackRoutine(attacks=tuple(attacks)))
                if not faithful:
                    incomplete = True
            elif segment.strip():
                incomplete = True
    derived = ["attacks"] if incomplete or (bool(lines) and not routines) else []
    return tuple(routines), derived


def map_stat_block(
    template_id: str, name: str, block: RawStatBlock, max_keyed_count: int = 1
) -> tuple[MonsterTemplate, tuple[str, ...]]:
    """Map one usable raw block onto a `MonsterTemplate` under the pinned anchors — deterministic, total.

    Every field is either traceably printed or recorded in the returned
    derived list: AC complements by 19, THAC0/attack bonus and XP derive from
    the HD tables when unprinted, saves derive via the printed save-as, the
    class table, or the monster save bands, morale defaults to 7 (the 2d6
    mean), alignment to neutral, number appearing to the maximum fixed keyed
    count, movement to the B/X human norm, and treasure is always the empty
    ref — keyed treasure is already the area's, and inventing a treasure type
    would be invention.

    Args:
        template_id: The already-allocated template id.
        name: The normalized extracted name (the template's display name).
        block: A block satisfying [`usable_stat_block`][osrforge.assemble.usable_stat_block].
        max_keyed_count: The unprinted-NA fallback — the maximum fixed keyed
            count across the name's encounters, floor 1.

    Returns:
        The template and the sorted derived-field record.

    Raises:
        ValueError: If the block is not usable (programmer misuse — callers
            gate on the shared predicate).
    """
    ac_parsed = _parse_ac(block)
    hd_parsed = _parse_hd_text(block.hit_dice)
    class_parsed = _parse_class_level(block.class_level)
    if ac_parsed is None or (hd_parsed is None and class_parsed is None):
        raise ValueError(f"stat block for {name!r} is not usable — callers must gate on usable_stat_block")
    derived: list[str] = ["treasure"]
    ac, ac_ascending, complemented = ac_parsed
    if complemented:
        derived.append("ac")
    special_count = len(block.special)
    class_level = None
    if hd_parsed is not None:
        hit_dice, hd_derived = _map_hit_dice(hd_parsed, block.hp, special_count)
        derived.extend(hd_derived)
    else:
        assert class_parsed is not None
        class_level = class_parsed
        hit_dice = MonsterHitDice(count=class_parsed[1], asterisks=special_count, fixed_hp=block.hp)
        derived.append("hit_dice")
    thac0_parsed = _parse_thac0(block.thac0)
    if thac0_parsed is not None:
        thac0, attack_bonus = thac0_parsed
    else:
        thac0, attack_bonus = thac0_for_hd(hit_dice.count, bonus_modifier=hit_dice.modifier > 0)
        derived.append("thac0")
    saves, saves_derived = _map_saves(block.saves, hit_dice, class_level)
    derived.extend(saves_derived)
    if block.morale is not None:
        morale = block.morale
    else:
        morale = 7
        derived.append("morale")
    alignment = None
    if block.alignment is not None:
        lowered = block.alignment.strip().casefold()
        for option in (Alignment.LAWFUL, Alignment.NEUTRAL, Alignment.CHAOTIC):
            if lowered.startswith(option.value[0]):
                alignment = option
                break
    if alignment is None:
        alignment = Alignment.NEUTRAL
        derived.append("alignment")
    number_appearing = _parse_number_appearing(block.number_appearing)
    if number_appearing is None:
        fallback = NumberAppearingValue(fixed=max(1, max_keyed_count))
        number_appearing = NumberAppearing(dungeon=fallback, lair=fallback)
        derived.append("number_appearing")
    movement, movement_derived = _map_movement(block.movement)
    derived.extend(movement_derived)
    attacks, attacks_derived = _map_attacks(block.attacks)
    derived.extend(attacks_derived)
    if block.xp is not None:
        xp = block.xp
    else:
        xp = monster_xp(load_combat_tables(), hit_dice)
        derived.append("xp")
    template = MonsterTemplate(
        id=template_id,
        name=name,
        page=f"p. {block.source_pages[0]}" if block.source_pages else "",
        ac=ac,
        ac_ascending=ac_ascending,
        hit_dice=hit_dice,
        attacks=attacks,
        thac0=thac0,
        attack_bonus=attack_bonus,
        movement=movement,
        saves=saves,
        morale=morale,
        alignment=AlignmentSpec(options=(alignment,)),
        xp=xp,
        number_appearing=number_appearing,
        treasure=TreasureRef(),
        abilities=tuple(
            MonsterAbility(tag="custom", name=line, prose=line, manual=True) for line in block.special if line.strip()
        ),
    )
    return template, tuple(sorted(set(derived)))


def _template_slug(name: str) -> str:
    """Slug a normalized name into the catalog's `^[a-z][a-z0-9_]*$` id convention."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_").lstrip("0123456789_")
    return slug or "custom_monster"


def _max_keyed_count(name: str, levels: Sequence[LevelContent]) -> int:
    """The unprinted-NA fallback: the maximum fixed keyed count across the name's encounters, floor 1."""
    counts = [
        encounter.count_fixed
        for level in levels
        for area in level.areas
        for encounter in area.encounters
        if normalize_monster_name(encounter.monster) == name and encounter.count_fixed is not None
    ]
    return max(counts, default=1)


def emit_custom_templates(
    resolutions: MonsterResolutions,
    blocks: Mapping[str, RawStatBlock | None],
    forced: Collection[str],
    levels: Sequence[LevelContent],
    base_ids: Collection[str],
) -> tuple[MonsterResolutions, dict[str, EmittedTemplate]]:
    """Emit custom templates for the unresolved names (plus the forced ones) with usable blocks — pure.

    A name with a usable candidate block gets an emitted template and an
    in-memory `method="custom"` resolution; a forced name (a
    `monster_templates:` entry) enters the population even when the tiers
    resolved it — forcing emission is the human's remedy for a flagless wrong
    LLM pick — and if its block is nonetheless unusable, the discarded pick is
    not restored: the name falls to the stand-in machinery flagged
    `monster_unresolved`, exactly as the refusal ladder treats an extracted
    unusable block, and completing the block is the designed remedy. Ids slug
    from the name with deterministic numeric suffixes on collision (against
    the base catalog and sibling emissions alike).

    Args:
        resolutions: The effective resolutions (monster remaps applied).
        blocks: The candidate blocks (template overrides applied).
        forced: Normalized names whose `monster_templates:` entries force emission.
        levels: The content caches (the unprinted-NA fallback's input).
        base_ids: The base catalog's template ids.

    Returns:
        The resolutions with emissions applied, and normalized name →
        emitted-template record.
    """
    population = sorted(
        {name for name, entry in resolutions.resolutions.items() if entry.template_id is None} | set(forced)
    )
    replaced = dict(resolutions.resolutions)
    emitted: dict[str, EmittedTemplate] = {}
    taken = set(base_ids)
    for name in population:
        block = blocks.get(name)
        if not usable_stat_block(block):
            if replaced[name].template_id is not None:
                # A forced name whose candidate block refuses emission: the
                # human rejected the cached pick, so it does not silently return.
                replaced[name] = MonsterResolution(template_id=None, method="unresolved")
            continue
        assert block is not None
        slug = _template_slug(name)
        template_id = slug
        suffix = 2
        while template_id in taken:
            template_id = f"{slug}_{suffix}"
            suffix += 1
        taken.add(template_id)
        template, derived = map_stat_block(template_id, name, block, _max_keyed_count(name, levels))
        emitted[name] = EmittedTemplate(template=template, derived=derived, source_pages=block.source_pages)
        replaced[name] = MonsterResolution(template_id=template_id, method="custom")
    return MonsterResolutions(resolutions=replaced), emitted


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
    """Map one encounter's count fields onto osrlib's exactly-one-of rule."""
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
    with a `low_confidence` flag — the [frozen stage-cache schema][frozen-schema]
    does not forbid an empty monster string, there is nothing to resolve or
    stand in for, and the monsters stage excludes it from the resolution
    population the same way.
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
            if resolution.method == "custom":
                monster_flags.append(format_flag(Flag.MONSTER_CUSTOM, name))
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
    override: AreaOverride | None,
    cells_overridden: bool,
) -> tuple[AreaSpec, AreaReport, list[str]]:
    """Build one `AreaSpec` and its report entry; returns them plus the area's unresolved names.

    An overridden field replaces the cache-derived value before any parsing —
    an overridden `encounter` skips encounter building entirely, an overridden
    `trap` skips the trap mapping, and the treasure grammar runs only for the
    slots (`treasure`, `features`) not overridden, so every flag falls out of
    the build path actually taken. When `content` is `None` (the extraction
    placeholder: the model skipped the area twice, or the level had no pages —
    the survey index remains the authority on what exists), the `not extracted`
    flag persists even under overrides: it describes extraction, like
    `confidence` and `source_pages`, which also stay the cache's.
    """
    cells = geometry.areas[area_key]
    fields = {name for name in AREA_OVERRIDE_FIELDS if override is not None and name in override.model_fields_set}
    low_confidence: list[str] = []
    monster_flags: list[str] = []
    treasure_flags: list[str] = []
    unresolved: list[str] = []

    if content is None:
        low_confidence.append(format_flag(Flag.LOW_CONFIDENCE, "not extracted"))
        confidence = 0.0
        source_pages = survey_pages
    else:
        confidence = content.confidence
        source_pages = content.source_pages

    name = area_name
    if override is not None and "name" in fields:
        name = override.name if override.name is not None else ""
    description = content.description if content is not None else ""
    if override is not None and "description" in fields:
        description = override.description if override.description is not None else ""

    if override is not None and "encounter" in fields:
        # The osrlib payload is used verbatim; the human's word is the last word.
        encounter = override.encounter
    elif content is not None:
        encounter, unresolved = _build_encounter(content, resolutions, table, settings, low_confidence, monster_flags)
    else:
        encounter = None

    if override is not None and "trap" in fields:
        trap = override.trap
    elif content is not None and content.trap is not None:
        trap = TrapSpec(kind="room", trigger="enter", affects="triggerer", effect=TrapEffect(manual=content.trap))
    else:
        trap = None

    # The treasure grammar has two outputs and each override controls exactly
    # one: `treasure` owns the AreaSpec.treasure slot (letters or the
    # best-effort unguarded fallback), `features` owns the final features tuple
    # wholesale — the mapped `-f{n}` features *and* the parsed `-treasure`
    # cache. The grammar runs only for the slots not overridden; leftovers it
    # did produce still describe strings the draft couldn't place.
    treasure_overridden = "treasure" in fields
    features_overridden = "features" in fields
    parsed = None
    if not (treasure_overridden and features_overridden):
        parsed = parse_treasure(content.treasure if content is not None else ())
        treasure_flags.extend(format_flag(Flag.TREASURE_UNPARSED, text) for text in parsed.unparsed)

    if features_overridden:
        assert override is not None
        features: tuple[FeatureSpec, ...] = override.features if override.features is not None else ()
    elif content is not None:
        assert parsed is not None
        feature_list = [
            FeatureSpec(id=f"{area_key}-f{number}", kind="custom", description=text)
            for number, text in enumerate(content.features, start=1)
        ]
        if parsed.coins.total_coins or parsed.valuables:
            feature_list.append(
                FeatureSpec(
                    id=f"{area_key}-treasure",
                    kind="treasure_cache",
                    coins=parsed.coins,
                    valuables=parsed.valuables,
                )
            )
        features = tuple(feature_list)
    else:
        features = ()

    if treasure_overridden:
        assert override is not None
        treasure = override.treasure
    else:
        assert parsed is not None
        treasure = None
        if parsed.letters:
            treasure = AreaTreasureSpec(letters=parsed.letters)
        elif parsed.unparsed and settings.unresolved_fallback == "best-effort":
            # The fallback never fires for an area whose treasure is overridden.
            treasure = AreaTreasureSpec(unguarded=True)

    spec = AreaSpec(
        id=area_key,
        name=name,
        description=description,
        cells=cells,
        encounter=encounter,
        features=features,
        trap=trap,
        treasure=treasure,
    )

    connection_flags = [
        format_flag(Flag.CONNECTION_AMBIGUOUS, detail)
        for key, detail in geometry.unresolved_connections
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
    transition_flags = [
        format_flag(Flag.TRANSITION_GUESSED, detail) for key, detail in geometry.guessed_transitions if key == area_key
    ]

    flags: list[str] = []
    if not cells_overridden:
        flags.append(format_flag(Flag.GEOMETRY_SYNTHESIZED))
    for group in (low_confidence, monster_flags, connection_flags, transition_flags, treasure_flags):
        flags.extend(dict.fromkeys(group))
    overridden = [name for name in AREA_OVERRIDE_FIELDS if name in fields]
    if cells_overridden:
        overridden.append("cells")
    report = AreaReport(
        id=address,
        source_pages=source_pages,
        confidence=confidence,
        flags=tuple(flags),
        overridden=tuple(overridden),
    )
    return spec, report, unresolved


def _build_added_area(area_key: str, override: AreaOverride, cells: tuple[Position, ...]) -> AreaSpec:
    """Build a human-authored added area — the one place a human statement *is* the extraction."""
    fields = override.model_fields_set
    return AreaSpec(
        id=area_key,
        name=override.name if override.name is not None else "",
        description=override.description if override.description is not None else "",
        cells=cells,
        encounter=override.encounter if "encounter" in fields else None,
        features=override.features if "features" in fields and override.features is not None else (),
        trap=override.trap if "trap" in fields else None,
        treasure=override.treasure if "treasure" in fields else None,
    )


@dataclass(frozen=True)
class DraftResult:
    """`build_draft`'s output: the adventure plus everything report production needs from the build."""

    adventure: Adventure
    area_reports: tuple[AreaReport, ...]
    module_flags: tuple[str, ...]
    unresolved: tuple[str, ...]


def _overridden_text(extracted: str, override: TownOverride | ModuleOverride | None, field: str) -> str:
    """One replaceable text field: absent leaves the extracted value, `null` clears it.

    A cleared field returns the build to its extracted-empty path, so the
    default (and its `low_confidence:… unstated` flag) applies exactly as it
    would to an extraction that came up empty.
    """
    if override is None or field not in override.model_fields_set:
        return extracted
    value: str | None = getattr(override, field)
    return value if value is not None else ""


def _tombstone(address: str, survey_pages: tuple[int, ...], content: AreaContent | None) -> AreaReport:
    """A removed area's report entry: id, cache facts, no flags — nothing was built."""
    return AreaReport(
        id=address,
        source_pages=content.source_pages if content is not None else survey_pages,
        confidence=content.confidence if content is not None else 0.0,
        flags=(),
        overridden=("removed",),
    )


def build_draft(
    index: SurveyIndex,
    levels: tuple[LevelContent, ...],
    resolutions: MonsterResolutions,
    geometries: tuple[LevelGeometry, ...],
    settings: ConversionSettings,
    plan: OverridePlan | None = None,
    custom_templates: Mapping[str, MonsterTemplate] | None = None,
) -> DraftResult:
    """Build the draft adventure and per-area reports from validated caches plus overrides — pure.

    Args:
        index: The survey cache.
        levels: Every level's content cache, in survey order.
        resolutions: The monsters cache, monster overrides and template
            emission already applied; every keyed encounter name must have an
            entry.
        geometries: The effective geometry (synthesized, overrides applied),
            in survey order.
        settings: The run's settings echo (`unresolved_fallback`,
            `blank_page_renders`).
        plan: The resolved override plan; `None` means no correction file.
        custom_templates: Emitted template id → template. Only templates a
            built encounter actually references bundle into
            `Adventure.monsters` (sorted by id) — an emission every reference
            was remapped away from is dead content and stays out.

    Returns:
        The draft, its per-area reports in survey order (removed areas keep a
        tombstone entry; added areas append after their level's survey areas),
        the module-scope flags, and the sorted unresolved names.

    Raises:
        ValueError: If an encounter name is missing from the resolutions — a
            stale cache (`convert`'s ordering makes it unreachable).
    """
    plan = plan if plan is not None else OverridePlan()
    module_flags: list[str] = []
    # Pure by construction: the settings echo in `run.json` is already an
    # assembly input, so the blanked-page flags derive from it, not from
    # inspecting render bytes.
    module_flags.extend(
        format_flag(Flag.PAGE_UNREADABLE, f"page {page} render blanked")
        for page in sorted(set(settings.blank_page_renders))
    )
    name = _overridden_text(index.title, plan.module, "name")
    if not name:
        name = "Untitled module"
        module_flags.append(format_flag(Flag.LOW_CONFIDENCE, "module title unstated"))
    description = _overridden_text(index.description, plan.module, "description")
    hooks = index.hooks
    if plan.module is not None and "hooks" in plan.module.model_fields_set:
        hooks = plan.module.hooks if plan.module.hooks is not None else ()
    town_name = _overridden_text(index.town.name, plan.town, "name")
    if not town_name:
        town_name = "Town"
        module_flags.append(format_flag(Flag.LOW_CONFIDENCE, "town name unstated"))
    services = index.town.services
    if plan.town is not None and "services" in plan.town.model_fields_set:
        services = plan.town.services if plan.town.services is not None else ()
    travel_turns: dict[str, int] = {}
    if plan.town is not None and "travel_turns" in plan.town.model_fields_set:
        travel_turns = plan.town.travel_turns if plan.town.travel_turns is not None else {}
    town = TownSpec(
        name=town_name,
        description=_overridden_text(index.town.description, plan.town, "description"),
        services=services,
        travel_turns=travel_turns,
    )

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
            level_plan = plan.levels.get((survey_dungeon.id, survey_level.number))
            content = contents.get((survey_dungeon.id, survey_level.number))
            content_by_key = {area.key: area for area in content.areas} if content is not None else {}
            table = tables.for_level(survey_level.number)
            areas: list[AreaSpec] = []
            for survey_area in survey_level.areas:
                address = f"{survey_dungeon.id}/{survey_level.number}/{survey_area.key}"
                if level_plan is not None and survey_area.key in level_plan.removed:
                    area_reports.append(
                        _tombstone(address, survey_area.source_pages, content_by_key.get(survey_area.key))
                    )
                    continue
                spec, report, area_unresolved = _build_area(
                    address=address,
                    area_key=survey_area.key,
                    area_name=survey_area.name,
                    survey_pages=survey_area.source_pages,
                    content=content_by_key.get(survey_area.key),
                    geometry=geometry,
                    resolutions=resolutions,
                    table=table,
                    settings=settings,
                    override=level_plan.area_overrides.get(survey_area.key) if level_plan is not None else None,
                    cells_overridden=level_plan is not None and survey_area.key in level_plan.cells,
                )
                areas.append(spec)
                area_reports.append(report)
                unresolved.update(area_unresolved)
            for added_key, added_override in level_plan.adds if level_plan is not None else ():
                areas.append(_build_added_area(added_key, added_override, geometry.areas[added_key]))
                area_reports.append(
                    AreaReport(
                        id=f"{survey_dungeon.id}/{survey_level.number}/{added_key}",
                        source_pages=(),
                        confidence=1.0,
                        flags=(),
                        overridden=("added",),
                    )
                )
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

    referenced = {
        keyed.template_id
        for dungeon in dungeons
        for level in dungeon.levels
        for area in level.areas
        if area.encounter is not None
        for keyed in area.encounter.monsters
    }
    bundled = tuple(
        sorted(
            (template for template_id, template in (custom_templates or {}).items() if template_id in referenced),
            key=lambda template: template.id,
        )
    )
    adventure = Adventure(
        name=name, description=description, hooks=hooks, town=town, dungeons=tuple(dungeons), monsters=bundled
    )
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
    """Run stage 5: overrides application, geometry synthesis, the adventure build, validation, and the artifact writes.

    Args:
        workdir_path: The workdir root; its monsters stage must be `completed`
            and every stage cache present. `overrides.yaml`, when present, is
            applied — a missing file is an empty overrides set.

    Returns:
        The draft adventure and its extraction report, as written to
        `adventure.json` and `report.json` (plus one preview per level).

    Raises:
        ValueError: If the monsters stage is not `completed`, a cache is
            missing, or the monsters or stat-block cache is stale against the
            upstream caches (programmer misuse — `convert`'s ordering makes
            these unreachable).
        OverrideError: If an override entry cannot take effect — raised before
            any `run.json` or artifact write. A `monster_templates:` entry
            against a workdir with no `statblocks.json` (a pre-phase-7
            workdir) or an `off` knob echo fails here: an explicit correction
            silently suppressed by a missing cache or a setting would be a
            silent no-op, the worst outcome.
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
    # The stat-block cache's three read paths, pinned: file missing → no
    # emission and no error (every pre-phase-7 workdir still assembles); echo
    # `off` → no emission; echo `emit` with an unresolved name missing from
    # `blocks` → the stale-cache hard error, mirroring the monsters check above.
    statblocks: StatBlocks | None = None
    if workdir.statblocks_json.is_file():
        statblocks = StatBlocks.model_validate_json(workdir.statblocks_json.read_text(encoding="utf-8"))
    if statblocks is not None and statblocks.custom_monsters == "emit":
        cached_unresolved = {name for name, entry in resolutions.resolutions.items() if entry.template_id is None}
        missing_blocks = sorted(cached_unresolved - statblocks.blocks.keys())
        if missing_blocks:
            raise ValueError(
                f"the stat-block cache is stale — no entry for unresolved names: {missing_blocks}; re-run monsters"
            )
    overrides = load_overrides(workdir.overrides_yaml)
    template_entries = plan_template_overrides(overrides, resolutions)
    if template_entries:
        if statblocks is None:
            raise OverrideError(
                "monster template overrides address stages/statblocks.json, which this workdir does not have — "
                "re-run monsters"
            )
        if statblocks.custom_monsters == "off":
            raise OverrideError(
                "monster template overrides cannot take effect under custom_monsters: off — "
                "re-run monsters with --set custom_monsters=emit"
            )
    resolutions = apply_monster_overrides(resolutions, overrides)
    emitted: dict[str, EmittedTemplate] = {}
    if statblocks is not None and statblocks.custom_monsters == "emit":
        candidate_blocks = apply_template_overrides(dict(statblocks.blocks), template_entries)
        resolutions, emitted = emit_custom_templates(
            resolutions,
            candidate_blocks,
            frozenset(template_entries),
            levels,
            frozenset(template.id for template in load_monsters().monsters),
        )
    plan = plan_overrides(index, overrides)

    with track_stage(workdir, Stage.GEOMETRY):
        geometries = tuple(
            apply_level_overrides(geometry, plan.levels.get((geometry.dungeon_id, geometry.level_number)))
            for geometry in synthesize_geometry(index, levels)
        )
    with track_stage(workdir, Stage.ASSEMBLE):
        draft = build_draft(
            index,
            levels,
            resolutions,
            geometries,
            run.settings,
            plan,
            custom_templates={record.template.id: record.template for record in emitted.values()},
        )
        validation = _run_validation(draft.adventure)
        resolved_count = sum(1 for resolution in resolutions.resolutions.values() if resolution.template_id is not None)
        usage = TokenUsage()
        for stage in (Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS):
            stage_usage = run.stages[stage].usage
            if stage_usage is not None:
                usage = usage + stage_usage
        bundled_ids = {template.id for template in draft.adventure.monsters}
        custom_records = tuple(
            CustomMonsterRecord(
                id=record.template.id, name=name, source_pages=record.source_pages, derived=record.derived
            )
            for name, record in sorted(emitted.items(), key=lambda item: item[1].template.id)
            if record.template.id in bundled_ids
        )
        report = ExtractionReport(
            module=ModuleInfo(title=index.title, pages=run.page_count),
            validation=validation,
            areas=draft.area_reports,
            monsters=MonsterSummary(resolved=resolved_count, unresolved=draft.unresolved, custom=custom_records),
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

    Re-runs geometry synthesis and override application over the survey and
    content caches plus `overrides.yaml` and rewrites `previews/` only,
    touching neither the other artifacts nor `run.json`. The rendered bytes
    are identical to assembly's — previews follow the draft, so corrected
    cells and override-authored doors render here too.

    Args:
        workdir_path: The workdir root; the survey and content caches must be
            present.

    Returns:
        The written preview paths, in survey order.

    Raises:
        ValueError: If the survey or a level's content cache is missing.
        OverrideError: If an override entry cannot take effect.
    """
    workdir = Workdir(workdir_path)
    index, levels = _load_caches(workdir)
    overrides = load_overrides(workdir.overrides_yaml)
    plan = plan_overrides(index, overrides)
    geometries = tuple(
        apply_level_overrides(geometry, plan.levels.get((geometry.dungeon_id, geometry.level_number)))
        for geometry in synthesize_geometry(index, levels)
    )
    geometry_by_address = {(geometry.dungeon_id, geometry.level_number): geometry for geometry in geometries}
    workdir.previews_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for dungeon in index.dungeons:
        for survey_level in dungeon.levels:
            geometry = geometry_by_address[(dungeon.id, survey_level.number)]
            level_plan = plan.levels.get((dungeon.id, survey_level.number))
            level = LevelSpec(
                number=survey_level.number,
                width=geometry.width,
                height=geometry.height,
                edges=geometry.edges,
                areas=tuple(
                    AreaSpec(id=key, name=name, cells=geometry.areas[key])
                    for key, name in effective_roster(survey_level, level_plan)
                ),
                transitions=geometry.transitions,
                entrance=geometry.entrance,
            )
            path = workdir.preview_svg(dungeon.id, survey_level.number)
            path.write_text(render_level_svg(dungeon.id, level), encoding="utf-8")
            written.append(path)
    return tuple(written)
