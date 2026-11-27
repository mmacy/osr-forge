"""Check a full-run report's arithmetic from the report alone. It prints each check and exits 1 on a failure.

The checks are the following:

- Each member's split by style adds up, family by family, to its whole score, over every cell and without the
  partly-floor cells.
- Each pool's every family equals its members' splits of that style added up, and its levels, levels that need no
  repair, incomplete levels, and costs equal its level rows'.
- Each scored panel's parts, one per level of the key and one for the cells no level owns, add up to the panel's
  score, and each level row's reading families add up its parts of the panels.
- Each member's level rows add up to its whole keys and key joins.
- The bar charges every reading's joins on the same levels, and its settled edges are no more than its edges.
- Each level is marked complete exactly when it has no unjudged item and no wall made for want of a Clef answer.
- The level rows and the named rows add up to the tally's totals by part and origin, request for request and dollar
  for dollar, and to the report's own `rows_added_up`.

Run from the osr-forge checkout:

    uv run python -m tools.reading.full_report_check <report.json>
"""

import json
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = ["check_report", "main"]

ORIGINS = ("sent", "full run", "screening", "answer-key pass", "fake", "missing")
READING = ("floor", "edges", "doors")


def _flat(figures: Mapping[str, Any] | None, prefix: str = "") -> dict[str, float]:
    """Every count of a family tree by its dotted path, leaving out the mean overlap, which doesn't add up."""
    out: dict[str, float] = {}
    for name, value in (figures or {}).items():
        if name == "mean_overlap" or value is None or isinstance(value, bool):
            continue
        if isinstance(value, Mapping):
            out.update(_flat(value, f"{prefix}{name}."))
        elif isinstance(value, (int, float)):
            out[f"{prefix}{name}"] = value
    return out


def _sum(trees: list[Mapping[str, Any] | None]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for tree in trees:
        for name, value in _flat(tree).items():
            out[name] += value
    return dict(out)


def _differences(one: Mapping[str, float], other: Mapping[str, float]) -> list[str]:
    return [
        f"{name} {one.get(name, 0)} against {other.get(name, 0)}"
        for name in sorted({*one, *other})
        if abs(one.get(name, 0) - other.get(name, 0)) > 1e-6
    ]


def check_report(report: Mapping[str, Any], out: Any = sys.stdout) -> int:
    """Check a report and print each check.

    Args:
        report: The report.
        out: Where to print.

    Returns:
        The number of failures.
    """
    fails = 0

    def check(label: str, ok: bool, details: list[str] | None = None) -> None:
        nonlocal fails
        out.write(f"{'ok  ' if ok else 'FAIL'} {label}{': ' + '; '.join(details[:5]) if details else ''}\n")
        fails += not ok

    levels: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report["levels"]:
        levels[row["member"]].append(row)

    for member, figures in report["members"].items():
        if "after" not in figures:
            # A member whose page is unread, or that didn't resolve, has no scores to add up.
            check(f"{member}: no scores ({figures['resolution'][:60]})", True)
            continue
        for part in ("after", "after_whole"):
            split = _sum([styles[part] for styles in figures["by_style"].values()])
            wrong = _differences(split, _flat(figures[part]))
            check(f"{member}: its split by style adds up to its whole score ({part})", not wrong, wrong)
        rows = levels.get(member, [])
        for family, count in (("keys", "total"), ("joins", "key")):
            mine = sum(row["after"][family][count] for row in rows)
            whole = figures["after"][family][count]
            check(f"{member}: its level rows' {family} {mine} add up to its whole {whole}", mine == whole)
        for panel_id, panel in figures["panels"].items():
            if panel["after"] is None:
                continue
            for part in ("after", "after_whole"):
                parts = _sum([owner[part] for owner in panel["parts"].values()])
                wrong = _differences(parts, _flat(panel[part]))
                check(f"{member} {panel_id}: its parts add up to the panel ({part})", not wrong, wrong)
        for row in rows:
            label = f"{row['dungeon']}/{row['level']}"
            for part in ("after", "after_whole"):
                parts = _sum(
                    [
                        panel["parts"][label][part]
                        for panel in figures["panels"].values()
                        if panel["parts"] and label in panel["parts"]
                    ]
                )
                mine = _flat({name: row[part][name] for name in READING})
                wrong = _differences(mine, parts)
                check(
                    f"{member} {label}: its reading families add up its parts of the panels ({part})", not wrong, wrong
                )

    for name, pool in report["pools"].items():
        style, kind = name.rsplit(" ", 1)
        members = [
            member
            for member, figures in report["members"].items()
            if style in figures.get("by_style", {}) and figures["synthetic"] == (kind == "synthetic")
        ]
        for part in ("after", "after_whole"):
            theirs = _sum([report["members"][member]["by_style"][style][part] for member in members])
            wrong = _differences(_flat(pool.get(part)), theirs)
            check(f"{name}: every family equals its members' ({part})", not wrong, wrong)
        rows = [row for row in report["levels"] if row["style"] == style and row["synthetic"] == (kind == "synthetic")]
        for field, counted in (
            ("levels", len(rows)),
            ("needs_no_repair", sum(row["needs_no_repair"] for row in rows)),
            ("key_needs_no_repair", sum(row["key_checks"]["needs_no_repair"] for row in rows)),
            ("incomplete", sum(not row["complete"] for row in rows)),
        ):
            check(f"{name}: {field} {pool[field]} = rows {counted}", pool[field] == counted)
        wrong = _differences(_flat(pool.get("costs")), _sum([row["costs"] for row in rows]))
        check(f"{name}: its costs add up its level rows'", not wrong, wrong)

    for style, row in (report.get("bar") or {}).items():
        keys = {
            reader: row[reader].get("joins", {}).get("key", 0)
            for reader in ("stage", "stage_on_key_reading", "classifier")
            if not row[reader].get("left_out")
        }
        check(f"bar {style}: every reading's joins cover the same levels {keys}", len(set(keys.values())) <= 1)
        for reader in ("stage", "classifier"):
            settled, edges = row["disputed"][reader], row[reader].get("edges", {})
            over = [name for name, value in settled.items() if value > edges.get(name, 0)]
            check(f"bar {style}: the {reader}'s settled edges are no more than its edges", not over, over)

    incomplete = [
        f"{row['member']} {row['dungeon']}/{row['level']}"
        for row in report["levels"]
        if row["complete"] != (row["unjudged_items"] == 0 and row["walls_without_clef"] == 0)
    ]
    check("every level's complete flag matches its counts", not incomplete, incomplete)

    added = {part: {origin: [0, 0.0] for origin in ORIGINS} for part in ("claude", "clef")}
    for costs in [*(row["costs"] for row in report["levels"]), *report["costs"]["named_rows"].values()]:
        for part in ("claude", "clef"):
            for origin in ORIGINS:
                added[part][origin][0] += costs[part][origin]["requests"]
                added[part][origin][1] += costs[part][origin]["usd"]
    tally = {part: {origin: [0, 0.0] for origin in ORIGINS} for part in ("claude", "clef")}
    for name, figures in report["costs"]["requests"].items():
        kind, origin = name.split(" ", 1)
        part = "clef" if kind.startswith("clef") else "claude"
        tally[part][origin][0] += figures["requests"]
        tally[part][origin][1] += figures["usd"]
    for part in ("claude", "clef"):
        for origin in ORIGINS:
            mine, theirs = added[part][origin], tally[part][origin]
            own = report["costs"]["rows_added_up"][part][origin]
            if mine[0] or theirs[0] or mine[1] or theirs[1]:
                check(
                    f"{part} {origin}: rows {mine[0]} requests ${mine[1]:.6f} = tally {theirs[0]} ${theirs[1]:.6f}",
                    mine[0] == theirs[0] and abs(mine[1] - theirs[1]) < 1e-6,
                )
                check(
                    f"{part} {origin}: rows = the report's rows_added_up",
                    own["requests"] == mine[0] and abs(own["usd"] - mine[1]) < 1e-6,
                )
    check(f"the report says its rows add up: {report['costs']['rows_add_up']}", report["costs"]["rows_add_up"] is True)
    out.write(f"{fails} failures\n")
    return fails


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: python -m tools.reading.full_report_check <report.json>\n")
        return 2
    return 1 if check_report(json.loads(Path(args[0]).read_text())) else 0


if __name__ == "__main__":
    raise SystemExit(main())
