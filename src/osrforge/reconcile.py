"""Deterministic reconciliation of prose edge facts with map proposals, plus the shared entrance selection.

Pure code consumed by **both** geometry synthesis and the eval scorer — the
shared-predicate precedent ([`transition_via`][osrforge.geometry.transition_via],
[`usable_stat_block`][osrforge.assemble.usable_stat_block]) extended from a
function to the whole policy, so the two consumers can never merge or pick
differently. The shared code owns the map-versus-prose policy only — the
prose side of the merge stays each consumer's own derivation (geometry's
`_GraphEdge` merge, the scorer's edge-fact dedup), including their recorded
door-kind asymmetry on two-mention edges.

The precedence rule, the doctrine extended across evidence sources: **a
stated prose fact survives any map conflict, flagged; a prose absence fills
from the map, flagged as adopted; agreement is silent.** The `door: "none"`
corner, pinned: a proposed pair's door reading is complete (the truth
convention's own posture), so a prose-stated door on a pair the map proposes
with `door: "none"` is a presence disagreement — prose survives, flagged —
while prose with no stated *door* on that pair agrees silently, whatever its
non-door mechanism (the map's vocabulary doesn't carry mechanisms).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from osrforge.contracts.stages import MapLevelReading, SurveyDungeon
from osrforge.survey import canonical_slug

__all__ = [
    "EntranceSelection",
    "MergedLevelEdges",
    "ProseEdge",
    "merge_level_edges",
    "resolve_key",
    "select_entrance",
]

_DOOR_KINDS = ("door", "secret_door")

_DOOR_RANK = {"none": 0, "door": 1, "secret_door": 2}
"""The duplicate-proposal dedup order: the more specific door reading wins."""


@dataclass(frozen=True)
class ProseEdge:
    """One undirected prose edge's merged facts, as the consumer derived them.

    Attributes:
        stated_via: The merged stated (non-`passage`) mechanism, or `None` —
            the absence test, matching geometry's: `via == "passage"` on
            every mention is the only absence.
        door_kind: The merged prose door kind (`door` / `secret_door`), or
            `None` when the prose states no door. Each consumer's own merge
            supplies it — the recorded first-stated vs more-specific
            asymmetry stays put.
    """

    stated_via: str | None = None
    door_kind: Literal["door", "secret_door"] | None = None


@dataclass(frozen=True)
class MergedLevelEdges:
    """One level's merge outcome, in the shapes both consumers map from.

    Attributes:
        adopted_doors: Undirected pair → the map door kind adopted onto an
            existing prose edge whose mentions stated no mechanism.
        map_only: `(first, second, door kind or None)` per adopted map-only
            pair, endpoints ordered survey-order-first, in proposal order.
        disputes: `(survey-order-first endpoint key, detail)` per
            disagreement and adoption, the flag emission home's addressing.
        dropped: Bare details for dropped proposals (unresolvable endpoints,
            self-pairs) — the caller prefixes its level address.
    """

    adopted_doors: Mapping[frozenset[str], Literal["door", "secret_door"]]
    map_only: tuple[tuple[str, str, Literal["door", "secret_door"] | None], ...]
    disputes: tuple[tuple[str, str], ...]
    dropped: tuple[str, ...]


def resolve_key(raw: str, level_keys: Sequence[str]) -> str | None:
    """Resolve a stated key against one level's canonical keys: exact match, else slug match.

    *The* endpoint-resolution rule, public for the shared-predicate reason:
    geometry resolves prose connection targets and this module resolves map
    proposal endpoints through the same function, so the two evidence
    sources can never resolve one printed spelling differently.

    Args:
        raw: The key as stated (a prose `to_key` or a map proposal endpoint).
        level_keys: The level's canonical keys.

    Returns:
        The resolved canonical key, or `None`.
    """
    if raw in level_keys:
        return raw
    slug = canonical_slug(raw)
    if slug and slug in level_keys:
        return slug
    return None


def _pair_label(pair: frozenset[str], position: Mapping[str, int]) -> tuple[str, str]:
    """A pair's endpoints in survey order — the dispute grammar's `<a>–<b>` and the flag's emission home.

    The en-dash separator is the point: canonical keys contain hyphens, so a
    hyphen could not delimit the pair unambiguously.
    """  # noqa: RUF002 — the en-dash is the grammar's pair separator
    first, second = sorted(pair, key=lambda key: position[key])
    return first, second


def merge_level_edges(
    prose: Mapping[frozenset[str], ProseEdge],
    reading: MapLevelReading | None,
    level_keys: Sequence[str],
) -> MergedLevelEdges:
    """Merge one level's prose edge facts with its map proposals under the precedence rule.

    Proposal normalization first: each endpoint resolves exactly-then-slug
    against `level_keys`; unresolvable endpoints and self-pairs drop into
    `dropped` with a detail; duplicate proposals of one pair dedup with the
    more-specific door winning (`secret_door` beats `door` beats `none` —
    the scorer's precedent). Then, per undirected pair, the enumerated rule:

    - agreement → silent;
    - a map door onto a prose edge with no stated mechanism → adopted,
      flagged;
    - a prose-stated door (or kind, or non-door mechanism) contradicted by
      the map → prose survives, flagged;
    - a prose edge absent from a read map → kept, flagged;
    - a map-only pair → adopted as an edge (plus its door), flagged.

    An `unread` (or absent) reading produces no adoptions and no
    disagreements — the prose-only path, bit for bit.

    Args:
        prose: Undirected pair → the consumer's own merged prose fact.
        reading: The level's map reading, or `None` (no cache — a
            pre-phase-11 workdir).
        level_keys: The level's canonical keys in survey order — the
            resolution universe and the endpoint-ordering authority.

    Returns:
        The merge outcome; every field empty when the level is unread.
    """
    if reading is None or reading.status != "read":
        return MergedLevelEdges(adopted_doors={}, map_only=(), disputes=(), dropped=())
    position = {key: index for index, key in enumerate(level_keys)}
    dropped: list[str] = []
    proposals: dict[frozenset[str], Literal["none", "door", "secret_door"]] = {}
    for proposal in reading.proposals:
        endpoints: list[str] = []
        unresolved = False
        for raw in (proposal.a, proposal.b):
            resolved = resolve_key(raw, level_keys)
            if resolved is None:
                dropped.append(f"map names '{raw}'; the survey does not")
                unresolved = True
            else:
                endpoints.append(resolved)
        if unresolved:
            continue
        if endpoints[0] == endpoints[1]:
            dropped.append(f"map joins '{endpoints[0]}' to itself")
            continue
        pair = frozenset(endpoints)
        existing = proposals.get(pair)
        if existing is None or _DOOR_RANK[proposal.door] > _DOOR_RANK[existing]:
            proposals[pair] = proposal.door

    adopted_doors: dict[frozenset[str], Literal["door", "secret_door"]] = {}
    map_only: list[tuple[str, str, Literal["door", "secret_door"] | None]] = []
    disputes: list[tuple[str, str]] = []
    for pair, door in proposals.items():
        first, second = _pair_label(pair, position)
        fact = prose.get(pair)
        if fact is None:
            adopted = door if door in _DOOR_KINDS else None
            map_only.append((first, second, adopted))
            reading_text = f"map-proposed ({adopted})" if adopted is not None else "map-proposed"
            disputes.append((first, f"edge {first}–{second} {reading_text}; prose states no connection — adopted"))  # noqa: RUF001 — en-dash pair separator
            continue
        if door in _DOOR_KINDS:
            if fact.door_kind == door:
                continue  # agreement is silent
            if fact.stated_via is None:
                adopted_doors[pair] = door  # a prose absence fills from the map
                disputes.append((first, f"door {first}–{second} map-read {door}; prose states no mechanism — adopted"))  # noqa: RUF001 — en-dash pair separator
            elif fact.door_kind is not None:
                disputes.append((first, f"door {first}–{second} prose-stated {fact.door_kind}; map-read {door} — kept"))  # noqa: RUF001 — en-dash pair separator
            else:
                disputes.append(
                    (first, f"door {first}–{second} map-read {door}; prose states {fact.stated_via} — kept")  # noqa: RUF001 — en-dash pair separator
                )
        elif fact.door_kind is not None:
            # A proposed pair's door reading is complete: "none" disputes a
            # stated door; a stated non-door mechanism agrees silently.
            disputes.append((first, f"door {first}–{second} prose-stated {fact.door_kind}; the map shows none — kept"))  # noqa: RUF001 — en-dash pair separator
    for pair in prose:
        if pair in proposals:
            continue
        first, second = _pair_label(pair, position)
        disputes.append((first, f"edge {first}–{second} prose-stated; the map shows no adjacency — kept"))  # noqa: RUF001 — en-dash pair separator
    return MergedLevelEdges(
        adopted_doors=adopted_doors,
        map_only=tuple(map_only),
        disputes=tuple(disputes),
        dropped=tuple(dropped),
    )


@dataclass(frozen=True)
class EntranceSelection:
    """One dungeon's entrance selection — the single source geometry and the scorer consume.

    Attributes:
        level_number: The selected entrance level, or `None` when the
            dungeon has no non-empty level.
        area_key: The selected entrance area's canonical key, or `None`
            with `level_number`.
        provenance: `map` when a resolvable map proposal won, else
            `heuristic` (the positional pick, or nothing to select).
        dispute: The both-picks detail when the map candidate and the
            heuristic differ (`entrance map <key>, survey-order <key>`), or
            `None` — flagged on the *selected* entrance area.
        dropped: `(level number, detail)` per dropped proposal —
            unresolvable keys and secondary resolvable proposals — the
            no-silent-proposals rule's channel.
    """

    level_number: int | None
    area_key: str | None
    provenance: Literal["map", "heuristic"]
    dispute: str | None
    dropped: tuple[tuple[int, str], ...]


def select_entrance(dungeon: SurveyDungeon, readings: Mapping[int, MapLevelReading]) -> EntranceSelection:
    """Select one dungeon's entrance: a resolvable map proposal wins over the positional heuristic.

    The heuristic pick is the first listed area of the lowest-numbered
    non-empty level — geometry's zero-evidence guess, kept as the fallback.
    The map candidate is the first resolvable `entrance_key` in level-number
    order over the dungeon's *read* levels, resolved exactly-then-slug
    against its own level's keys; evidence beats position, so a present
    candidate wins, with a dispute detail when the two differ. An
    unresolvable proposal drops with `map names '<key>'; the survey does
    not`; a *secondary* resolvable proposal (another level also labeling a
    way in) is neither adopted nor discarded silently — it drops with
    `entrance also proposed <level>/<key>`, a reviewable oddity.

    Args:
        dungeon: The surveyed dungeon.
        readings: Level number → the level's map reading, for this dungeon.

    Returns:
        The selection, provenance, and dispute channels.
    """
    heuristic_level = min((level.number for level in dungeon.levels if level.areas), default=None)
    heuristic_key: str | None = None
    if heuristic_level is not None:
        heuristic_key = next(level for level in dungeon.levels if level.number == heuristic_level).areas[0].key
    keys_by_number = {level.number: [area.key for area in level.areas] for level in dungeon.levels}

    candidate: tuple[int, str] | None = None
    dropped: list[tuple[int, str]] = []
    for number in sorted(keys_by_number):
        reading = readings.get(number)
        if reading is None or reading.status != "read" or reading.entrance_key is None:
            continue
        resolved = resolve_key(reading.entrance_key, keys_by_number[number])
        if resolved is None:
            dropped.append((number, f"map names '{reading.entrance_key}'; the survey does not"))
            continue
        if candidate is None:
            candidate = (number, resolved)
        else:
            dropped.append((number, f"entrance also proposed {number}/{resolved}"))

    if candidate is None:
        return EntranceSelection(
            level_number=heuristic_level,
            area_key=heuristic_key,
            provenance="heuristic",
            dispute=None,
            dropped=tuple(dropped),
        )
    dispute = None
    if (candidate[0], candidate[1]) != (heuristic_level, heuristic_key):
        dispute = f"entrance map {candidate[1]}, survey-order {heuristic_key}"
    return EntranceSelection(
        level_number=candidate[0],
        area_key=candidate[1],
        provenance="map",
        dispute=dispute,
        dropped=tuple(dropped),
    )
