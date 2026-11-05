"""Pool the stored Claude outcomes into one table per kind of request, by model and effort.

Run from the osr-forge checkout after the drivers:

    uv run python -m tools.reading.screen_summary <dir> [--ledger <Claude ledger>] [--json <file>]

For each model and effort it reports, per kind, the requests, the malformed first answers, the answers still
malformed after the retry, the cost, the output tokens, and the seconds. It also reports each kind's own figures:

- whole-page reads: the region fields right (style, plan view, feet, north, compass printed, legend printed, cut-off
  edges), the outline's floor overlap with the key's, the seeds on the level's floor, and the symbols found and
  extra, in all and by kind
- offset picks: the right picks, on the real pairs and on the synthetic ones
- items: the readings right as passable or blocked (edges) or floor or not (cells), the exact letters, and the
  readings right on the first ten items of each map, which the ten-item requests asked

Beside the settings, `calibrated Clef and ink` scores the decision the items were chosen by on the same items: an
edge is passable when its calibrated P(passable) is at least one half, and a cell is floor when the ink finds it at
least half floor. With `--ledger`, it also counts the ledger's entries whose label starts with `reading-`, and the
ones charged nothing.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .claude_run import LABEL_PREFIX
from .items import letters_of

__all__ = ["ledger_counts", "main", "summarise"]

REGION_FIELDS = ("style", "feet", "north", "compass_printed", "legend_printed", "cut_off")


def _base(records: list[dict[str, Any]]) -> dict[str, Any]:
    attempts = [attempt for record in records for attempt in record["attempts"]]
    return {
        "requests": len(attempts),
        "first_malformed": sum(record["malformed_first"] for record in records),
        "still_malformed": sum(record["answer"] is None for record in records),
        "cost_usd": round(sum(record["cost_usd"] for record in records), 4),
        "output_tokens": sum((attempt["usage"] or {}).get("output_tokens", 0) for attempt in attempts),
        "seconds": round(sum(attempt["seconds"] for attempt in attempts), 1),
    }


def summarise(root: Path) -> dict[str, Any]:
    """Every kind's figures by model and effort."""
    out: dict[str, Any] = defaultdict(dict)
    for setting in sorted((root / "wholemap").glob("*/*")):
        model, effort = setting.parent.name, setting.name
        records, region_right, regions = [], defaultdict(int), 0
        ious, seeds, seeds_in, found, key_symbols, extra, direction = [], 0, 0, 0, 0, 0, 0
        by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for path in sorted(setting.glob("*.json")):
            if path.name.endswith(".score.json"):
                continue
            records.append(json.loads(path.read_text()))
            score_path = path.with_suffix(".score.json")
            if not score_path.exists():
                continue
            score = json.loads(score_path.read_text())
            for figures in score["regions"].values():
                regions += 1
                for field in REGION_FIELDS:
                    read, key = figures[field]
                    region_right[field] += read == key
                region_right["plan_view"] += figures["plan_view"] is True
            for level in score["levels"].values():
                if level.get("region_right"):
                    ious.append(level["outline_iou"])
                    seeds += level["seeds"]
                    seeds_in += level["seeds_in_level"]
            for symbols in score["symbols"].values():
                found += symbols["found"]
                key_symbols += symbols["key"]
                extra += symbols["extra"]
                direction += symbols["direction_right"]
                for kind, (key_count, found_count) in symbols["by_kind"].items():
                    by_kind[kind][0] += found_count
                    by_kind[kind][1] += key_count
        out[f"{model} {effort}"]["wholemap"] = _base(records) | {
            "regions": regions,
            "right": dict(region_right),
            "outline_iou_mean": round(sum(ious) / len(ious), 3) if ious else None,
            "levels": len(ious),
            "seeds_in_level": f"{seeds_in}/{seeds}",
            "symbols_found": f"{found}/{key_symbols}",
            "symbols_by_kind": {kind: f"{n}/{k}" for kind, (n, k) in sorted(by_kind.items())},
            "symbols_extra": extra,
            "directions_right": direction,
        }
    for setting in sorted((root / "offset").glob("*/*")):
        model, effort = setting.parent.name, setting.name
        records = [json.loads(path.read_text()) for path in sorted(setting.glob("*.json"))]
        picks = {path.stem: json.loads(path.read_text()) for path in sorted(setting.glob("*.json"))}

        def real(name: str, picks: dict[str, Any] = picks) -> bool:
            return bool(picks[name].get("real"))

        right_real = sum((r["answer"] or {}).get("choice") == r["truth"] for name, r in picks.items() if real(name))
        right_synthetic = sum(
            (r["answer"] or {}).get("choice") == r["truth"] for name, r in picks.items() if not real(name)
        )
        wrong_real = sum(
            (r["answer"] or {}).get("choice") not in (r["truth"], "none") for name, r in picks.items() if real(name)
        )
        out[f"{model} {effort}"]["offset"] = _base(records) | {
            "real_right": f"{right_real}/{sum(real(name) for name in picks)}",
            "real_wrong_pick": wrong_real,
            "synthetic_right": f"{right_synthetic}/{sum(not real(name) for name in picks)}",
            "picks": {name: (r["answer"] or {}).get("choice") for name, r in picks.items()},
        }
    items_file = root / "items" / "items.json"
    if items_file.exists():
        chosen = json.loads(items_file.read_text())
        out["calibrated Clef and ink"]["items"] = _calibrated(chosen)
        for setting in sorted(path for path in (root / "items").glob("*/*") if path.is_dir()):
            model, effort = setting.parent.name, setting.name
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for path in sorted(setting.glob("*.json")):
                tag = path.stem.split(".", 1)[1]
                groups["" if tag[:1].isdigit() and "k" not in tag else tag.lstrip("0123456789")].append(
                    json.loads(path.read_text()) | {"name": path.stem}
                )
            for tag, records in groups.items():
                tallies = defaultdict(int)
                for record in records:
                    if record["answer"] is None:
                        continue
                    map_id, part = record["name"].split(".", 1)
                    per = 30 if "k30" in part else 10
                    start = int(part.split("k")[0]) * per if part[:1].isdigit() else 0
                    items = chosen[map_id][start : start + per]
                    letters = letters_of(record["answer"])
                    for number, item in enumerate(items, start=1):
                        read = letters.get(number, "-")
                        kind = "edge" if item["side"] else "cell"
                        tallies[f"{kind}s"] += 1
                        if kind == "edge":
                            right = (read in "op") == (item["key"] in "op")
                            tallies["edges exact"] += read == item["key"]
                        else:
                            right = (read == ".") == (item["key"] == ".")
                        tallies[f"{kind}s right"] += right
                        if start + number <= 10:
                            tallies["first ten"] += 1
                            tallies["first ten right"] += right
                out[f"{model} {effort}"][f"items{tag}"] = _base(records) | dict(tallies)
    return dict(out)


def _calibrated(chosen: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """The choosing decision's figures on the chosen items: edges by P(passable), cells by the ink's half rule."""
    tallies: dict[str, int] = defaultdict(int)
    for items in chosen.values():
        for number, item in enumerate(items, start=1):
            kind = "edge" if item["side"] else "cell"
            right = (item["clef"] >= 0.5) == (item["key"] in ("op" if kind == "edge" else "."))
            tallies[f"{kind}s"] += 1
            tallies[f"{kind}s right"] += right
            if number <= 10:
                tallies["first ten"] += 1
                tallies["first ten right"] += right
    return dict(tallies)


def ledger_counts(path: Path) -> dict[str, int]:
    """The ledger's entries whose label starts with `reading-`, and the ones charged nothing."""
    entries = [e for e in json.loads(path.read_text())["entries"] if str(e.get("label", "")).startswith(LABEL_PREFIX)]
    return {"entries": len(entries), "charged nothing": sum(entry.get("cost_usd") == 0 for entry in entries)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The screening's Claude figures by model and effort.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--ledger", type=Path, default=None, help="the Claude ledger, read and never written")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    summary = summarise(args.root)
    if args.ledger is not None:
        summary["ledger"] = {"reading": ledger_counts(args.ledger)}
    for setting, kinds in summary.items():
        for kind, figures in kinds.items():
            print(setting, kind, json.dumps(figures))
    if args.json:
        args.json.write_text(json.dumps(summary, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
