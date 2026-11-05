"""The reading measurement's full run (step 8 of the plan's measurements): every map read, resolved, scored, checked.

It covers every map with a first answer key and every synthetic map (`full_members`), reads each as the stage reads
it with the screening's settings (`full_reading`, `full_settings`), resolves each member's reading into a trace with
the reference resolution script (`full_score.resolve_member`), scores the trace against its key with `score_trace`,
and runs the structural checks (`full_checks`). The report gives the following:

- every scorer family per level, over every cell (`after`) and without the partly-floor cells (`after_whole`), on the
  level's own documents (`full_score.level_documents`). Every calibration a map is read with was fitted without the
  maps cut from its image, so every score is a held-out score.
- every family pooled by style, real maps apart from synthetic ones, each drawn cell, edge, level, and pair once
  (`full_pools.pools`), and the bar each style of the answer keys must meet, the stage beside the classifier
  (`full_pools.bar_table`)
- whether each level needs no repair by the checks, beside the same checks on the answer key
- whether each level's reading is complete: the items Claude left without an answer, and the edges between floor
  that became walls for want of a Clef answer, both counted on the level
- every request the run counted, once each, on exactly one level or on a row the report names for what no level owns
  (`full_costs`), by origin: sent by this run, answered by an earlier invocation, reused from the screening or the
  answer-key pass, fake, or missing. The rows add up to the run's totals, and a live run's totals are checked against
  the ledgers' entries.
- Claude's accuracy on the items per style and kind: the screening's, which weights each answer, and this run's
  against the keys

Run from the osr-forge checkout. `R` is the results directory and `P` the commercial specs:

    uv run python -m tools.reading.full_run --settings $R/full-run/settings.json --out $R/full-run \\
        --maps tools/reading/maps.json --maps $P/<module>-maps.json --pages tools/reading/pages.json \\
        --pages $P/<module>-pages.json --keys tools/answer_keys/keys.json --keys <module keys file> \\
        --halves $P/halves.json --synthetic tests/assets/synthetic-maps --screening $R --classifier $R/classifier \\
        [--dry-run | --claude none|fake|live --clef none|fake|local|local-only|hosted] [--pictures]

`--dry-run` sends nothing: it reads with every stored answer, lets the answer key's whole-page answer stand in where
none is stored, and estimates the requests and dollars each map still needs. `--claude live` needs
`OSRFORGE_CLAUDE_LEDGER` to name the settings' ledger, and is held to the settings' cap on that ledger's `reading-`
entries. `--claude-for <member>` keeps Claude to the named members' maps and pages (and the real pairs), so a run
within the cap can take the members in an order. `--clef local` asks local Clef-flash and, for a question it fails
on, hosted Clef-flash within the settings' hosted spend (`--clef local-only` without the fallback). Hosted Clef needs
`OSRFORGE_CLEF_ACCOUNT_ID`, `OSRFORGE_CLEF_LEDGER`, and a wrangler login.
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.tracing.score import score_trace
from osrforge.tracing.truth import GeometryTruth

from .claude_run import LABEL_PREFIX, RequestCap
from .full_checks import LevelChecks, check_document
from .full_costs import allocate, place_cell, row_totals
from .full_members import HalvesSpec, Member, load_halves, real_members, synthetic_members
from .full_pools import bar_table, panel_scores, pools
from .full_reading import (
    MapReading,
    PageRead,
    Pick,
    calibrate,
    choose_items,
    clef_edges,
    image_digest,
    map_letters,
    measure_ink,
    member_reading,
    page_legends,
    pick_offsets,
    read_items,
    read_marks,
    read_page,
    screening_accuracy,
    start_map,
    unanswered_edges,
)
from .full_score import entrance_on_level, level_documents, resolve_member, summary
from .full_seed import seed_claude, seed_clef_exported, seed_clef_stores
from .full_settings import FullRunSettings, load_settings
from .full_store import ORIGINS, ClaudeStore, ClefAnswer, ClefStore, Sources

__all__ = [
    "RunResult",
    "classifier_trace",
    "estimate",
    "ledger_check",
    "ledger_snapshot",
    "main",
    "make_sources",
    "run",
]


@dataclass
class RunResult:
    """What a run read and scored."""

    report: dict[str, Any]
    traces: dict[str, GeometryTruth] = field(default_factory=dict)
    readings: dict[str, MapReading] = field(default_factory=dict)
    checks: dict[str, list[LevelChecks]] = field(default_factory=dict)


def _style_of(key: GeometryTruth, dungeon_id: str, number: int) -> str:
    level = next(lv for d in key.dungeons if d.id == dungeon_id for lv in d.levels if lv.number == number)
    for placement in level.panels:
        return key.region(key.panel(placement.panel).regions[0].region).style
    return "none"


def classifier_trace(member: Member, readings_dir: Path) -> GeometryTruth | None:
    """The classifier's trace of a real member: the key's reading with each panel's cells and edges the classifier's,
    resolved by the reference resolution script, or `None` when a map has no classifier reading."""
    from tools.answer_keys.resolve import load_caches, reading_of

    from .bar import classifier_panel

    key = member.document()
    screens = {run_map.screen.id: run_map.screen for run_map in member.maps}
    panels = []
    for panel in key.panels:
        screen = screens.get(panel.id)
        if screen is None or not (readings_dir / f"{screen.id}.json").exists():
            return None
        theirs = classifier_panel(readings_dir, screen)
        panels.append(panel.model_copy(update={"cells": theirs.cells, "edges": theirs.edges}))
    document = GeometryTruth.model_validate(reading_of(key).model_copy(update={"panels": tuple(panels)}).model_dump())
    survey, contents, mapread = load_caches(member.stages)
    trace, _how = resolve_member(document, survey, contents, mapread)
    return trace


def run(
    members: Sequence[Member],
    halves: Sequence[HalvesSpec],
    settings: FullRunSettings,
    sources: Sources,
    stand_in: bool,
    workers: int = 1,
    screening: Path | None = None,
    classifier: Path | None = None,
) -> RunResult:
    """Read, resolve, score, and check every member.

    Args:
        members: The members.
        halves: The real pairs of halves, which decide the halves rule.
        settings: The run's settings.
        sources: Where answers come from.
        stand_in: Let the answer key's whole-page answer stand in for a read the store lacks (a dry run).
        workers: How many Clef requests may be in flight at once.
        screening: The screening's results directory, whose item judgments give Claude's accuracy, or `None` to let
            Claude's answers decide.
        classifier: The classifier's readings directory, for the bar, or `None` to leave the classifier out.

    Returns:
        The report and what it was made from.
    """
    from tools.answer_keys.resolve import load_caches

    result = RunResult(report={})
    page_reads: dict[str, PageRead] = {}
    for member in members:
        key = member.document()
        reads = [read_page(page, settings, sources, key if stand_in else None) for page in member.pages]
        page_reads.update({read.page.id: read for read in reads})
        legends = page_legends(reads)
        for run_map in member.maps:
            reading = start_map(run_map, page_reads[run_map.page], legends.get(run_map.page))
            result.readings[run_map.uid] = reading
            if reading.style is not None:
                measure_ink(reading)
                clef_edges(reading, sources, workers)
    calibration = calibrate(list(result.readings.values()))
    real_styles = {run_map.uid: run_map.screen.style for m in members if not m.synthetic for run_map in m.maps}
    measured = (
        screening_accuracy(
            screening, settings.items.model, settings.items.effort, settings.items_per_request, real_styles
        )
        if screening is not None
        else {}
    )
    weights = {
        style: {kind: right / total for kind, (right, total) in kinds.items() if total}
        for style, kinds in measured.items()
    }
    pages = {page.id: page for member in members for page in member.pages}
    unanswered: Counter[str] = Counter()
    for uid, reading in result.readings.items():
        if reading.style is None:
            continue
        choose_items(reading, settings.epsilon)
        unanswered[f"{uid} item requests"] += read_items(reading, pages[reading.run_map.page], settings, sources)
        map_letters(reading, settings.thresholds[reading.style], weights.get(reading.style))
        unanswered[f"{uid} marks"] += read_marks(reading, pages[reading.run_map.page], sources, workers)
        unanswered[f"{uid} edges between floor"] += unanswered_edges(reading)
    picks = pick_offsets([*halves, *(spec for member in members for spec in member.halves)], settings, sources)
    halves_apart = any(pick.wrong for pick in picks if pick.spec.real)
    levels_report: list[dict[str, Any]] = []
    members_report: dict[str, Any] = {}
    panels: dict[str, dict[str, dict[str, Any]]] = {}
    member_halves: dict[str, dict[str, Any]] = {}
    traced: dict[str, set[tuple[str, int]]] = {}
    classifier_traces: dict[str, GeometryTruth] = {}
    classifier_levels: dict[tuple[str, str, int], dict[str, Any]] = {}
    for member in members:
        key = member.document()
        survey, contents, mapread = load_caches(member.stages)
        readings = {r.run_map.screen.id: r for r in result.readings.values() if r.run_map.member == member.id}
        mine = [pick for pick in picks if pick.spec.member == member.id]
        trace, resolution = resolve_member(
            member_reading(member, readings, mine, halves_apart), survey, contents, mapread
        )
        result.traces[member.id] = trace
        scores = score_trace(trace, key)
        trace_checks = check_document(trace, contents, mapread)
        key_checks = {(c.dungeon, c.level): c for c in check_document(key, contents, mapread)}
        result.checks[member.id] = trace_checks
        members_report[member.id] = {
            "resolution": resolution,
            "after": summary(scores.after),
            "after_whole": summary(scores.after_whole),
        }
        panels[member.id] = panel_scores(trace, key)
        pair_style = key.region(key.half_pairs[0].regions[0]).style if key.half_pairs else "none"
        member_halves[member.id] = {
            "style": pair_style,
            "total": scores.after.halves.total,
            "right": scores.after.halves.right,
        }
        traced[member.id] = {(d.id, lv.number) for d in key.dungeons for lv in d.levels if lv.verdict == "traced"}
        incomplete = _incomplete(readings.values(), traced[member.id])
        if classifier is not None and not member.synthetic:
            theirs = classifier_trace(member, classifier)
            if theirs is not None:
                classifier_traces[member.id] = theirs
        by_level = {(c.dungeon, c.level): c for c in trace_checks}
        for dungeon_id, number in sorted(traced[member.id]):
            one_trace, one_key = level_documents(trace, key, dungeon_id, number)
            level_score = score_trace(one_trace, one_key)
            after, after_whole = summary(level_score.after), summary(level_score.after_whole)
            if not entrance_on_level(key, dungeon_id, number):
                after["entrance"] = after_whole["entrance"] = None
            if member.id in classifier_traces:
                theirs_trace, theirs_key = level_documents(classifier_traces[member.id], key, dungeon_id, number)
                classifier_levels[(member.id, dungeon_id, number)] = summary(
                    score_trace(theirs_trace, theirs_key).after
                )
            checks = by_level.get((dungeon_id, number))
            gaps = incomplete.get((dungeon_id, number), {"unjudged_items": 0, "walls_without_clef": 0})
            levels_report.append(
                {
                    "member": member.id,
                    "synthetic": member.synthetic,
                    "dungeon": dungeon_id,
                    "level": number,
                    "style": _style_of(key, dungeon_id, number),
                    "after": after,
                    "after_whole": after_whole,
                    "checks": checks.to_json() if checks else {"traced": False},
                    "needs_no_repair": bool(checks and checks.needs_no_repair),
                    "key_checks": key_checks[(dungeon_id, number)].to_json(),
                    **gaps,
                    "complete": not gaps["unjudged_items"] and not gaps["walls_without_clef"],
                }
            )
        members_report[member.id]["incomplete outside every level"] = incomplete.get(None, {})
    rows = allocate(sources.tally, members, result.readings, traced, halves)
    for row in levels_report:
        row["costs"] = rows.get(f"{row['member']} {row['dungeon']}/{row['level']}") or _no_costs()
    named = {name: figures for name, figures in rows.items() if not _is_level_row(name, levels_report)}
    totals = sources.tally.totals()
    added = row_totals(rows)
    result.report = {
        "settings": {
            "whole_page": vars(settings.whole_page),
            "items": vars(settings.items) | {"per_request": settings.items_per_request},
            "offset_pick": vars(settings.offset_pick),
            "cap": settings.cap,
            "thresholds": settings.thresholds,
            "epsilon": settings.epsilon,
        },
        "calibration": calibration,
        "claude_accuracy": {
            "screening": measured,
            "weights": weights,
            "this_run": _claude_on_items(result.readings.values()),
        },
        "halves": [
            {
                "pair": pick.spec.id,
                "real": pick.spec.real,
                "choice": pick.choice,
                "truth": pick.truth,
                "wrong": pick.wrong,
                "same_pictures_as": [other.spec.id for other in picks if other is not pick and _same(other, pick)],
            }
            for pick in picks
        ],
        "halves_apart": halves_apart,
        "stand_in_pages": sorted(page for page, read in page_reads.items() if read.stand_in),
        "unread_pages": sorted(page for page, read in page_reads.items() if read.answer is None),
        "unanswered": {name: count for name, count in sorted(unanswered.items()) if count},
        "members": members_report,
        "levels": levels_report,
        "pools": pools(levels_report, panels, member_halves, {m.id: m.synthetic for m in members}),
        "bar": bar_table(
            [m for m in members if not m.synthetic], result.traces, classifier_traces, levels_report, classifier_levels
        )
        if classifier is not None
        else None,
        "costs": {
            "named_rows": named,
            "rows_added_up": added,
            "requests": totals,
            "rows_add_up": _agree(added, totals),
        },
    }
    return result


def _no_costs() -> dict[str, Any]:
    return {
        part: {origin: {"requests": 0, "usd": 0.0} for origin in ORIGINS} | {"answers": 0}
        for part in ("claude", "clef")
    }


def _is_level_row(name: str, levels: Sequence[dict[str, Any]]) -> bool:
    return any(name == f"{row['member']} {row['dungeon']}/{row['level']}" for row in levels)


def _agree(added: dict[str, Any], totals: dict[str, dict[str, Any]]) -> bool:
    """Whether the rows add up to the tally's totals, request for request and dollar for dollar."""
    for part in ("claude", "clef"):
        for origin in ORIGINS:
            mine = [
                figures
                for name, figures in totals.items()
                if name.endswith(f" {origin}") and name.startswith("clef" if part == "clef" else "reading-")
            ]
            requests = sum(figures["requests"] for figures in mine)
            usd = round(sum(figures["usd"] for figures in mine), 6)
            if added[part][origin]["requests"] != requests or abs(added[part][origin]["usd"] - usd) > 1e-6:
                return False
    return True


def _incomplete(
    readings: Sequence[MapReading] | Any, traced: set[tuple[str, int]]
) -> dict[tuple[str, int] | None, dict[str, int]]:
    """The items without a Claude answer and the walls without a Clef answer, on the level that holds each."""
    out: dict[tuple[str, int] | None, dict[str, int]] = defaultdict(
        lambda: {"unjudged_items": 0, "walls_without_clef": 0}
    )
    for reading in readings:
        for item in reading.unjudged:
            where = place_cell(reading, item.cell, traced)
            out[where if isinstance(where, tuple) else None]["unjudged_items"] += 1
        for edge in reading.unanswered:
            where = place_cell(reading, edge[0], traced)
            out[where if isinstance(where, tuple) else None]["walls_without_clef"] += 1
    return dict(out)


def _claude_on_items(readings: Any) -> dict[str, dict[str, list[int]]]:
    """Claude's answers on this run's items against the keys, per style and kind, as `[right, judged]`."""
    out: dict[str, dict[str, list[int]]] = {}
    for reading in readings:
        if reading.style is None:
            continue
        for (cell, side), letter in reading.judged.items():
            x, y = cell
            if side is None:
                kind, right = "cell", (letter == ".") == (reading.key.cells[y][x] == ".")
            else:
                kind, right = "edge", (letter in "op") == (reading.key.edges.at(cell, side) in "op")
            tally = out.setdefault(reading.style, {}).setdefault(kind, [0, 0])
            tally[0] += right
            tally[1] += 1
    return out


def _same(one: Pick, other: Pick) -> bool:
    """Whether two pairs' halves are the same image files byte for byte."""
    return (image_digest(one.spec.first), image_digest(one.spec.second)) == (
        image_digest(other.spec.first),
        image_digest(other.spec.second),
    )


# The dry run's estimate.


def estimate(result: RunResult, sources: Sources, settings: FullRunSettings, means: dict[str, float]) -> dict[str, Any]:
    """The requests and dollars each map and page still needs, from a run that sent nothing.

    Args:
        result: The dry run's result.
        sources: Its sources, whose tally counts the requests the stores lack, each once.
        settings: The run's settings.
        means: Dollars a request of each Claude kind, as the screening measured them at the run's settings.

    Returns:
        Per map and page, and in all, the requests the stores lack: Claude's (with the item requests' range when Clef
        answers are missing) and Clef's, and their dollars.
    """
    per: dict[str, dict[str, Any]] = defaultdict(dict)
    for entry in sources.tally.entries.values():
        if entry.origin == "missing":
            per[entry.owner][entry.kind] = per[entry.owner].get(entry.kind, 0) + 1
    unanswered = result.report.get("unanswered", {})
    for uid, reading in result.readings.items():
        if reading.style is None:
            continue
        extra = unanswered.get(f"{uid} edges between floor", 0)
        known = per[uid].get("reading-items", 0)
        per[uid]["reading-items at most"] = max(
            known, math.ceil((len(reading.items) + extra) / settings.items_per_request)
        )
        per[uid]["items"] = len(reading.items)
        per[uid]["reading-items"] = known
    totals: Counter[str] = Counter()
    for counts in per.values():
        for kind, count in counts.items():
            if kind.startswith(("reading-", "clef")):
                totals[kind] += count

    def dollars(items: str) -> float:
        return round(
            totals["reading-wholemap"] * means.get("reading-wholemap", 0.0)
            + totals[items] * means.get("reading-items", 0.0)
            + totals["reading-offset"] * means.get("reading-offset", 0.0),
            4,
        )

    claude = totals["reading-wholemap"] + totals["reading-items"] + totals["reading-offset"]
    at_most = totals["reading-wholemap"] + totals["reading-items at most"] + totals["reading-offset"]
    return {
        "per": {owner: counts for owner, counts in sorted(per.items())},
        "totals": dict(totals),
        "claude_requests": claude,
        "claude_requests_at_most": at_most,
        "claude_requests_with_one_retry_each": 2 * at_most,
        "usd": {"claude": dollars("reading-items"), "claude_at_most": dollars("reading-items at most")},
        "clef_questions": totals["clef-edge"] + totals["clef-mark"],
        "means": means,
    }


def screening_means(path: Path, settings: FullRunSettings) -> dict[str, float]:
    """Dollars a request of each kind at the run's settings, from the screening's summary."""
    data = json.loads(path.read_text())

    def mean(setting: Any, kind: str) -> float:
        figures = data.get(f"{setting.model} {setting.effort}", {}).get(kind)
        return round(figures["cost_usd"] / figures["requests"], 6) if figures and figures["requests"] else 0.0

    return {
        "reading-wholemap": mean(settings.whole_page, "wholemap"),
        "reading-items": mean(settings.items, "itemsk30"),
        "reading-offset": mean(settings.offset_pick, "offset"),
    }


# Ledgers.


def ledger_snapshot(path: Path, prefix: str, cost_key: str, count_key: str | None = None) -> dict[str, Any]:
    """The requests and dollars of a ledger's entries whose label (or `what`) starts with a prefix.

    Args:
        path: The ledger.
        prefix: The start of the entries' label, or of their `what`.
        cost_key: The entries' cost key.
        count_key: The key that counts a per-run entry's requests, as on the Clef ledger, or `None` when each entry is
            one request, as on the Claude ledger.

    Returns:
        `{"requests", "usd"}`, the dollars in whole micro-dollars as the ledger keeps them.
    """
    if not path.exists():
        return {"requests": 0, "usd": 0.0}
    entries = [
        entry
        for entry in json.loads(path.read_text())["entries"]
        if str(entry.get("label") or entry.get("what") or "").startswith(prefix)
    ]
    requests = sum(int(entry.get(count_key) or 0) for entry in entries) if count_key else len(entries)
    micros = sum(round(float(entry.get(cost_key) or 0.0) * 1_000_000) for entry in entries)
    return {"requests": requests, "usd": micros / 1_000_000}


# Sources.


def make_sources(args: argparse.Namespace, settings: FullRunSettings, members: Sequence[Member]) -> Sources:
    """The stores and the sources the arguments name.

    Raises:
        SystemExit: If a live Claude run's provider would write to another ledger than the settings', or the local
            server names another model for `clef-flash`.
    """
    clef_store = ClefStore(args.out / "clef.jsonl")
    claude_store = ClaudeStore(args.out / "claude")
    sources = Sources(clef_store=clef_store, claude_store=claude_store)
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    if args.claude == "fake" or args.clef == "fake":
        from .full_fakes import FakeClaude, FakeClef

        pages = {page.id: (page, member.document()) for member in members for page in member.pages}
        if args.claude == "fake":
            sources.claude, sources.claude_how = FakeClaude(maps, pages), "fake"
        if args.clef == "fake":
            sources.clef = FakeClef(maps)
    if args.claude == "live":
        from .claude_run import ask, make_provider

        named = os.environ.get("OSRFORGE_CLAUDE_LEDGER", "").strip()
        if settings.ledger is None or not named or Path(named).expanduser().resolve() != settings.ledger.resolve():
            raise SystemExit(
                f"OSRFORGE_CLAUDE_LEDGER ({named or 'unset'}) must name the settings' ledger ({settings.ledger}), "
                "so the cap counts the ledger the provider writes to"
            )
        provider = make_provider(settings.whole_page.model)
        cap = RequestCap(settings.ledger, settings.cap)

        def live_claude(request: Any, check: Any, about: dict[str, Any]) -> dict[str, Any]:
            return ask(provider, request, check, cap).to_json()

        sources.claude, sources.claude_how = live_claude, "sent"
    if args.clef in ("local", "local-only", "hosted"):
        sources.clef = clef_source(args.clef, settings)
        if args.clef != "local-only" and settings.hosted_model != "clef-flash":
            sources.clef_alternates = (settings.hosted_model,)
    return sources


HOSTED_LABEL = "reading full run"
"""The run label of the full run's entries on the Clef ledger."""


def clef_source(mode: str, settings: FullRunSettings) -> Any:
    """Local Clef-flash with hosted Clef-flash behind it (`local`), local alone (`local-only`), or hosted alone.

    The hosted provider is made on its first question, held to the settings' hosted spend on the Clef ledger, where it
    keeps one entry for the run (`HOSTED_LABEL`) and adds each request's settled cost to it. Hosted questions go one
    at a time, so each answer's dollars are what its question added to the run's entries. Each answer comes back with
    the request actually sent, so the store files it under that request's key.
    """
    import threading

    from osrforge.errors import ProviderError

    from .hosted_exported import for_model

    hosted: list[tuple[Any, Path]] = []
    one_at_a_time = threading.Lock()

    def hosted_provider() -> tuple[Any, Path]:
        if not hosted:
            from osrforge.providers.clef import ClefProvider, ClefSettings

            from .clefask import capped_stop

            base = ClefSettings.from_env()
            stop = capped_stop(base.ledger_path, base.ledger_stop_usd, settings.hosted_spend_usd)
            provider = ClefProvider(base.model_copy(update={"run_label": HOSTED_LABEL, "ledger_stop_usd": stop}))
            hosted.append((provider, base.ledger_path))
        return hosted[0]

    def ask_hosted(request: Any) -> ClefAnswer:
        sent = for_model(request, settings.hosted_model)  # type: ignore[arg-type]
        with one_at_a_time:
            provider, ledger = hosted_provider()
            before = ledger_snapshot(ledger, HOSTED_LABEL, "usd")["usd"]
            response = provider.decide(sent)
            usd = round(ledger_snapshot(ledger, HOSTED_LABEL, "usd")["usd"] - before, 6)
        return ClefAnswer(dict(response.probabilities), sent, "hosted", usd)

    if mode == "hosted":
        return lambda request, about: ask_hosted(request)
    from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings

    local_settings = LocalClefSettings.from_env()
    if local_settings.models.get("clef-flash") != "clef-flash":
        raise SystemExit(
            f"the local server names {local_settings.models.get('clef-flash')!r} for clef-flash, and the run files "
            "local answers under the clef-flash request's key: unset OSRFORGE_CLEF_LOCAL_MODEL"
        )
    local = LocalClefProvider(local_settings)

    def ask(request: Any, about: dict[str, Any]) -> ClefAnswer:
        try:
            return ClefAnswer(dict(local.decide(request).probabilities), request, "local")
        except ProviderError:
            if mode == "local-only":
                raise
            return ask_hosted(request)

    return ask


def seed(
    sources: Sources,
    members: Sequence[Member],
    halves: Sequence[HalvesSpec],
    screening: Path | None,
    settings: FullRunSettings,
) -> dict[str, Any]:
    """Seed the stores with the answer-key pass's and the screening's answers."""
    filed: dict[str, Any] = {"clef answer-key pass": 0, "clef screening": 0}
    for member in members:
        for run_map in member.maps:
            filed["clef answer-key pass"] += seed_clef_exported(sources.clef_store, run_map.uid, run_map.screen)
            if screening is not None:
                name = run_map.screen.id
                stores = [screening / "clef" / "local" / "keyset" / f"{name}.edge.jsonl"] if member.synthetic else []
                if not member.synthetic:
                    stores.append(screening / "clef" / "marks" / f"{name}.mark.jsonl")
                filed["clef screening"] += seed_clef_stores(sources.clef_store, run_map.uid, stores, "screening")
    if screening is not None:
        pages = [page for member in members if not member.synthetic for page in member.pages]
        specs = [*halves, *(spec for member in members for spec in member.halves)]
        filed["claude"] = seed_claude(
            sources.claude_store,
            screening / "claude",
            pages,
            (settings.whole_page.model, settings.whole_page.effort),
            specs,
            (settings.offset_pick.model, settings.offset_pick.effort),
        )
    return filed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The reading measurement's full run.")
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--maps", type=Path, action="append", default=[])
    parser.add_argument("--pages", type=Path, action="append", default=[])
    parser.add_argument("--keys", type=Path, action="append", default=[])
    parser.add_argument("--halves", type=Path, action="append", default=[])
    parser.add_argument("--synthetic", type=Path, default=None)
    parser.add_argument("--screening", type=Path, default=None, help="the screening's results directory, to seed from")
    parser.add_argument("--classifier", type=Path, default=None, help="the classifier's readings, for the bar")
    parser.add_argument("--only", action="append", default=None, help="a member id to run, of several")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--stand-in", action="store_true", help="let the key's whole-page answer stand in where none is stored"
    )
    parser.add_argument("--claude", choices=("none", "fake", "live"), default="none")
    parser.add_argument("--clef", choices=("none", "fake", "local", "local-only", "hosted"), default="none")
    parser.add_argument(
        "--claude-for", action="append", default=None, help="a member whose requests Claude answers, of several"
    )
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--ledger", type=Path, default=None, help="the Claude ledger a dry run counts the cap on")
    parser.add_argument("--pictures", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run and (args.claude != "none" or args.clef != "none"):
        parser.error("a dry run sends nothing: leave --claude and --clef at none")
    fake = args.claude == "fake" or args.clef == "fake"
    marker = args.out / "FAKE"
    stored = (args.out / "clef.jsonl").exists() or (args.out / "claude").exists()
    if fake and stored and not marker.exists():
        parser.error(f"{args.out} holds real answers, and a run on fake answers would mix its answers in")
    if not fake and marker.exists():
        parser.error(f"{args.out} holds fake answers, so a run on real ones can't use it")
    settings = load_settings(args.settings)
    members = real_members(args.maps, args.pages, args.keys) if args.maps else []
    if args.synthetic is not None:
        members += synthetic_members(args.synthetic)
    if args.only:
        members = [member for member in members if member.id in args.only]
    halves = [spec for path in args.halves for spec in load_halves(path) if spec.real]
    args.out.mkdir(parents=True, exist_ok=True)
    if fake:
        marker.write_text("The answers here include fake ones made from the answer keys.\n")
    sources = make_sources(args, settings, members)
    if args.claude_for:
        chosen = [member for member in members if member.id in args.claude_for]
        sources.claude_owners = {
            *(run_map.uid for member in chosen for run_map in member.maps),
            *(page.id for member in chosen for page in member.pages),
            *(spec.id for member in chosen for spec in member.halves),
            *(spec.id for spec in halves),
        }
    seeded = seed(sources, members, halves, args.screening, settings)
    print(f"seeded: {json.dumps(seeded)}", flush=True)
    claude_ledger = settings.ledger if args.claude == "live" else None
    clef_ledger_path = os.environ.get("OSRFORGE_CLEF_LEDGER", "").strip()
    clef_ledger = Path(clef_ledger_path).expanduser() if args.clef in ("local", "hosted") and clef_ledger_path else None
    before_claude = ledger_snapshot(claude_ledger, LABEL_PREFIX, "cost_usd") if claude_ledger else None
    before_clef = ledger_snapshot(clef_ledger, HOSTED_LABEL, "usd", "requests") if clef_ledger else None
    result = run(
        members,
        halves,
        settings,
        sources,
        stand_in=args.dry_run or args.stand_in,
        workers=args.workers,
        screening=args.screening,
        classifier=args.classifier,
    )
    result.report["seeded"] = seeded
    result.report["ledger_check"] = ledger_check(sources, (claude_ledger, before_claude), (clef_ledger, before_clef))
    name = "dry-run" if args.dry_run else f"report-claude-{args.claude}-clef-{args.clef}"
    name += "-stand-in" if args.stand_in and not args.dry_run else ""
    if args.dry_run:
        means = screening_means(args.screening / "claude" / "summary.json", settings) if args.screening else {}
        result.report["estimate"] = estimate(result, sources, settings, means)
        if args.ledger is not None:
            sent = RequestCap(args.ledger.expanduser(), settings.cap).sent()
            result.report["estimate"]["cap"] = {"cap": settings.cap, "sent": sent, "left": settings.cap - sent}
    (args.out / f"{name}.json").write_text(json.dumps(result.report, indent=1) + "\n")
    for member_id, trace in result.traces.items():
        safe = member_id.replace("/", "__")
        (args.out / "traces").mkdir(exist_ok=True)
        (args.out / "traces" / f"{safe}.{name}.json").write_text(trace.model_dump_json(indent=1) + "\n")
    print_report(result.report, sys.stdout)
    if args.pictures:
        from .full_pictures import milestone_pictures

        for path in milestone_pictures(result, members, args.out / "pictures", settings.milestone):
            print(f"picture: {path}")
    return 0


def ledger_check(
    sources: Sources,
    claude: tuple[Path | None, dict[str, Any] | None],
    clef: tuple[Path | None, dict[str, Any] | None],
) -> dict[str, Any]:
    """What this run added to each ledger, beside what its tally says it sent there.

    The Claude ledger has an entry per attempt, and the tally counts each sent outcome's attempts. The Clef ledger has
    an entry per run, counting its requests, and only hosted answers are on it, so the tally's side is the sent Clef
    answers the store records as hosted.

    Args:
        sources: The run's sources, with its tally and stores.
        claude: The Claude ledger and its snapshot before the run, or `None`s when the run sent Claude nothing.
        clef: The Clef ledger and its snapshot before the run (with its request counts), or `None`s.

    Returns:
        Per ledger: what the ledger gained, what the tally counted, and whether they agree.
    """
    out: dict[str, Any] = {}
    sent = [entry for entry in sources.tally.entries.values() if entry.origin == "sent"]
    claude_sent = [entry for entry in sent if not entry.kind.startswith("clef")]
    hosted = [
        entry
        for entry in sent
        if entry.kind.startswith("clef") and (sources.clef_store.record(entry.key) or {}).get("source") == "hosted"
    ]
    for part, (ledger, before), prefix, cost_key, count_key, counted in (
        ("claude", claude, LABEL_PREFIX, "cost_usd", None, claude_sent),
        ("clef", clef, HOSTED_LABEL, "usd", "requests", hosted),
    ):
        if ledger is None or before is None:
            continue
        after = ledger_snapshot(ledger, prefix, cost_key, count_key)
        made = {"requests": after["requests"] - before["requests"], "usd": round(after["usd"] - before["usd"], 6)}
        tally = {
            "requests": sum(entry.requests for entry in counted),
            "usd": round(sum(entry.usd for entry in counted), 6),
        }
        out[part] = {
            "ledger": made,
            "tally": tally,
            "agree": made["requests"] == tally["requests"] and abs(made["usd"] - tally["usd"]) < 1e-6,
        }
    return out


def print_report(report: dict[str, Any], out: Any) -> None:
    """The report's figures: members, one line per level, the pools, the bar, and the requests."""
    for member, figures in report["members"].items():
        out.write(f"{member}: {figures['resolution']}\n")
    out.write(
        "member | level | style | floor both/key | edges right/total (walls open, open blocked) | cave edges | keys | "
        "areas perfect | joins matched/key | reach | needs no repair (key) | unjudged items, walls without Clef | "
        "Claude sent, reused\n"
    )
    for level in report["levels"]:
        a = level["after"]
        e, c = a["edges"]["all"], a["edges"]["cave"]
        claude = level["costs"]["claude"]
        reused = sum(claude[o]["requests"] for o in ("full run", "screening", "answer-key pass"))
        out.write(
            f"{level['member']} | {level['dungeon']}/{level['level']} | {level['style']} | "
            f"{a['floor']['both']}/{a['floor']['key']} | {e['right']}/{e['total']} "
            f"({e['walls_read_as_open']}, {e['open_read_as_blocked']}) | {c['right']}/{c['total']} | "
            f"{a['keys']['right']}/{a['keys']['total']} | {a['areas']['perfect']}/{a['areas']['areas']} | "
            f"{a['joins']['matched']}/{a['joins']['key']} | "
            f"{a['reachability']['right']}/{a['reachability']['total']} | "
            f"{level['needs_no_repair']} ({level['key_checks']['needs_no_repair']}) | "
            f"{level['unjudged_items']}, {level['walls_without_clef']} | {claude['sent']['requests']}, {reused}\n"
        )
    for style, figures in report["pools"].items():
        out.write(f"pool {style}: {json.dumps(figures)}\n")
    if report.get("bar"):
        for style, figures in report["bar"].items():
            out.write(f"bar {style}: {json.dumps(figures)}\n")
    out.write(f"halves: {json.dumps(report['halves'])}, apart: {report['halves_apart']}\n")
    out.write(f"requests: {json.dumps(report['costs']['requests'])}\n")
    out.write(f"cost rows add up: {report['costs']['rows_add_up']}\n")
    if "estimate" in report:
        estimate_ = report["estimate"]
        out.write(f"estimate totals: {json.dumps(estimate_['totals'])}\n")
        out.write(
            f"estimate: Claude {estimate_['claude_requests']} to {estimate_['claude_requests_at_most']} requests "
            f"({estimate_['claude_requests_with_one_retry_each']} with one retry each), "
            f"{json.dumps(estimate_['usd'])}, Clef {estimate_['clef_questions']} questions, "
            f"cap {json.dumps(estimate_.get('cap'))}\n"
        )


if __name__ == "__main__":
    raise SystemExit(main())
