"""Seed the full run's stores with the answers the answer-key pass and the screening already have, by request key.

- **Clef, real maps.** The answer-key pass asked local Clef-flash its exported edge requests
  (`<work>/clef/requests/`) under the model name `clef`, and stored each answer with that request's key
  (`<work>/clef/answers.jsonl`). Each exported request is rebuilt and checked against its exported key, and its answer
  is filed under the key of the same request named for `clef-flash`, the model the run sends to and the one that
  answered (the answer records `clef-flash`). The run asks that request for those edges.
- **Clef, the screening.** The screening's stores (`clefask` edges and cells, and the mark questions) record each
  answer's request key, so each is filed under it.
- **Claude.** The screening's outcomes don't record the key of the request they answered, so no key can be checked
  against what was sent. Each whole-page read and offset pick is rebuilt with the builders the screening used, which
  are unchanged since, and filed under that request's key only when what can be checked holds: the outcome's model
  and effort are the run's setting for its kind, and its answer passes the rebuilt request's own check (each region
  and level of the rebuilt request answered once, every cell on its region's grid). An outcome that fails is left
  out and reported. The screening's item requests aren't seeded: the full run asks every uncertain item in its own
  requests, so no screening request recurs.
"""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .full_members import HalvesSpec
from .full_store import ClaudeStore, ClefStore
from .hosted_exported import checked_requests, exported_requests, for_model
from .maps import ScreenMap, load_rgb
from .offsets import LETTERS, check_offset, offset_kind, offset_request
from .screen_offsets import load_pair
from .screen_wholemap import Page, _page_regions, levels_on
from .wholemap import check_whole_map, whole_map_kind, whole_map_request

__all__ = ["seed_claude", "seed_clef_exported", "seed_clef_stores"]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def seed_clef_exported(store: ClefStore, uid: str, screen: ScreenMap) -> int:
    """File a real map's answer-key-pass answers under the run's request keys.

    Args:
        store: The Clef store.
        uid: The map's uid.
        screen: The map, with its work directory and frame.

    Returns:
        How many answers were filed.

    Raises:
        ValueError: If a rebuilt request's key differs from its exported key, or an answer came from another model.
    """
    if screen.work is None or not (screen.work / "clef" / "requests" / "requests.jsonl").exists():
        return 0
    requests = checked_requests(exported_requests(screen.work, screen.frame), screen.id)
    by_key = {request.key(): (name, request) for name, request in requests.items()}
    filed = 0
    for answer in _jsonl(screen.work / "clef" / "answers.jsonl"):
        if answer.get("kind") != "edge" or answer.get("key") not in by_key:
            continue
        if "clef-flash" not in str(answer.get("model", "")):
            raise ValueError(f"{screen.id} {answer['item']}: answered by {answer.get('model')}, not clef-flash")
        name, request = by_key[answer["key"]]
        store.put(for_model(request, "clef-flash").key(), uid, name, answer["probabilities"], "answer-key pass")
        filed += 1
    return filed


def seed_clef_stores(store: ClefStore, uid: str, paths: Iterable[Path], source: str) -> int:
    """File the answers of the screening's stores, each under its recorded request key."""
    filed = 0
    for path in paths:
        for record in _jsonl(path):
            if record.get("key"):
                store.put(record["key"], uid, record["item"], record["probabilities"], source)
                filed += 1
    return filed


def seed_claude(
    store: ClaudeStore,
    screening: Path,
    pages: Iterable[Page],
    whole_page: tuple[str, str],
    halves: Iterable[HalvesSpec],
    offset_pick: tuple[str, str],
) -> dict[str, Any]:
    """File the screening's whole-page reads and offset picks under their rebuilt requests' keys, once checked.

    Args:
        store: The Claude store.
        screening: The screening's Claude results directory (`claude/`).
        pages: The pages, each rebuilt as the screening asked it.
        whole_page: The whole-page read's `(model, effort)`.
        halves: The pairs of halves, each rebuilt as the screening asked it.
        offset_pick: The offset pick's `(model, effort)`.

    Returns:
        How many outcomes were filed, by kind, and the ones left out with the reason.
    """
    filed: dict[str, Any] = {"reading-wholemap": 0, "reading-offset": 0, "left out": []}
    model, effort = whole_page
    kind = whole_map_kind(effort, model)  # type: ignore[arg-type]
    for page in pages:
        path = screening / "wholemap" / model / effort / f"{page.id}.json"
        if not path.exists():
            continue
        outcome = json.loads(path.read_text())
        regions = _page_regions(page)
        levels = levels_on(page)
        labels = [region for region, _rgb, _registration in regions]
        fault = _setting_fault(outcome, model, effort) or _answer_fault(
            outcome, lambda answer, labels=labels, levels=levels: check_whole_map(answer, labels, levels)
        )
        if fault:
            filed["left out"].append(f"{page.id}: {fault}")
            continue
        request = whole_map_request(kind, load_rgb(page.image), regions, levels)
        store.put(kind.name, request.key(model), outcome | {"source": "screening"})
        filed[kind.name] += 1
    model, effort = offset_pick
    kind = offset_kind(effort, model)  # type: ignore[arg-type]
    for spec in halves:
        name = spec.id if spec.member is None else _screening_pair_name(spec)
        path = screening / "offset" / model / effort / f"{name}.json"
        if not path.exists():
            continue
        outcome = json.loads(path.read_text())
        fault = _setting_fault(outcome, model, effort) or _answer_fault(outcome, check_offset)
        if not fault and outcome["answer"] is not None and outcome["answer"]["choice"] not in (*LETTERS, "none"):
            fault = f"the choice {outcome['answer']['choice']!r} isn't a letter or none"
        if fault:
            filed["left out"].append(f"{spec.id}: {fault}")
            continue
        pair, _columns, _truth = load_pair(_pair_item(spec), Path("/"))
        request = offset_request(kind, pair)
        store.put(kind.name, request.key(model), outcome | {"source": "screening"})
        filed[kind.name] += 1
    return filed


def _setting_fault(outcome: dict[str, Any], model: str, effort: str) -> str | None:
    if (outcome.get("model"), outcome.get("effort")) != (model, effort):
        return f"asked of {outcome.get('model')} at {outcome.get('effort')}, not {model} at {effort}"
    return None


def _answer_fault(outcome: dict[str, Any], check: Any) -> str | None:
    answer = outcome.get("answer")
    if answer is None:
        return None
    faults = check(answer)
    return f"its answer fails the rebuilt request's check: {faults[0]}" if faults else None


def _screening_pair_name(spec: HalvesSpec) -> str:
    """The screening's name for a synthetic member's pair: the joined maps were asked as `fold` and `gatehouse`."""
    member = (spec.member or "").split("/")[-1]
    return member.removesuffix("-joined")


def _pair_item(spec: HalvesSpec) -> dict[str, str]:
    item = {"first": str(spec.first), "second": str(spec.second), "truth": spec.truth or "none"}
    if spec.first_fit is not None:
        item["first_fit"] = str(spec.first_fit)
    if spec.second_fit is not None:
        item["second_fit"] = str(spec.second_fit)
    return item
