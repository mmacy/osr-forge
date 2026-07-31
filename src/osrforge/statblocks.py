"""Shared structural parsing of printed stat-block lines — AC, Hit Dice, and class-level notations.

These parsers are the pipeline's one reading of a printed block's structure,
shared by two consumers below and above the stage boundary: assembly's
deterministic template mapping ([`map_stat_block`][osrforge.assemble.map_stat_block]
and its refusal-ladder predicate
[`usable_stat_block`][osrforge.assemble.usable_stat_block]) and the monsters
stage's stat-block veto
([`stat_block_veto`][osrforge.monsters.stat_block_veto]). They live in their
own module because `assemble.py` imports `monsters.py` — the veto could never
import them from assembly without a cycle, and the veto must change the
*cached* resolution, so it cannot run in assembly.

Parsing is structural transcription of a printed notation, never a rules
judgment: every judgment (AC complement direction hazards, HD-to-`MonsterHitDice`
anchors, class-table derivations) stays with the consumer that owns it.
"""

import re
from dataclasses import dataclass

from osrforge.contracts.stages import RawStatBlock

__all__ = [
    "ParsedHd",
    "parse_ac",
    "parse_class_level",
    "parse_hd_text",
]

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


@dataclass(frozen=True)
class ParsedHd:
    """A printed Hit Dice line, structurally parsed: count, printed die (if any), modifier, asterisks."""

    count: int
    die: int | None
    modifier: int
    asterisks: int
    fractional: bool


def parse_ac(block: RawStatBlock) -> tuple[int, int, bool] | None:
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


def parse_hd_text(text: str | None) -> ParsedHd | None:
    """Structurally parse a printed HD line (`3+1`, `1-1`, `3*`, `½`, `2d8`), or None."""
    if text is None or not text.strip():
        return None
    stripped = text.strip()
    asterisks = stripped.count("*")
    if _HD_FRACTION.search(stripped):
        return ParsedHd(count=0, die=None, modifier=0, asterisks=asterisks, fractional=True)
    match = _HD_MAIN.search(stripped)
    if match is None:
        return None
    die = int(match.group(2)) if match.group(2) else None
    modifier = int(match.group(3).replace(" ", "")) if match.group(3) else 0
    return ParsedHd(count=int(match.group(1)), die=die, modifier=modifier, asterisks=asterisks, fractional=False)


def parse_class_level(text: str | None) -> tuple[str, int] | None:
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
