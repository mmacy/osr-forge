"""The full run's answers: stores keyed by each request's key, and where an answer comes from when the store lacks it.

Every Clef answer and Claude outcome the run uses is filed under its request's key (`DecisionRequest.key()`, and
`ReadingRequest.key(model)` for the kind's model), so an answer is reused only for a request whose pixels, text,
options, model, and parameters are the same:

- [`ClefStore`][tools.reading.full_store.ClefStore] is a JSON-lines file of `{key, map, item, probabilities, source}`.
- [`ClaudeStore`][tools.reading.full_store.ClaudeStore] holds one JSON file per request, `<kind>/<key>.json`, with the
  outcome (its attempts, answer, and cost) and where it came from.

The stores are seeded with the screening's and the answer-key pass's stored answers, each under the key of the request
rebuilt the way it was asked (see `full_seed`). A request the store lacks goes to the run's
[`Sources`][tools.reading.full_store.Sources]: live (local Clef-flash, hosted Clef within its spend, Claude within the
cap), fake (made by a function, for tests and offline runs), or nothing, when a dry run only counts what it would ask.
Every request is tallied by map or page, kind, and how it was answered.
"""

import json
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.providers.decisions import DecisionRequest
from osrforge.providers.reading import ReadingRequest

__all__ = ["ClaudeStore", "ClefStore", "Sources", "Tally"]

type ClefAsk = Callable[[DecisionRequest, dict[str, Any]], dict[str, float]]
"""Answers a request with its probabilities by option id. The second argument says what the request is about."""

type ClaudeAsk = Callable[[ReadingRequest, Callable[[dict[str, Any]], list[str]], dict[str, Any]], dict[str, Any]]
"""Answers a request with an outcome's JSON: `attempts`, `answer` (`None` when malformed after the retry), `cost_usd`.
The third argument says what the request is about."""


class ClefStore:
    """Clef answers by request key, in a JSON-lines file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._answers: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    record = json.loads(line)
                    self._answers[record["key"]] = record

    def get(self, key: str) -> dict[str, float] | None:
        """The probabilities stored for a request key, or `None`."""
        record = self._answers.get(key)
        return None if record is None else record["probabilities"]

    def source(self, key: str) -> str | None:
        """Where a stored answer came from."""
        record = self._answers.get(key)
        return None if record is None else record.get("source")

    def put(self, key: str, map_uid: str, item: str, probabilities: dict[str, float], source: str) -> None:
        """Store an answer, unless one is stored under the key already."""
        with self._lock:
            if key in self._answers:
                return
            record = {"key": key, "map": map_uid, "item": item, "probabilities": probabilities, "source": source}
            self._answers[key] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")

    def __len__(self) -> int:
        return len(self._answers)


class ClaudeStore:
    """Claude outcomes by kind and request key, one JSON file each."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.json"

    def get(self, kind: str, key: str) -> dict[str, Any] | None:
        """The stored outcome for a request key, or `None`."""
        path = self._path(kind, key)
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, kind: str, key: str, record: dict[str, Any]) -> None:
        """Store an outcome, unless one is stored under the key already."""
        path = self._path(kind, key)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record | {"key": key}, indent=1) + "\n")


@dataclass
class Tally:
    """Requests by `(owner, kind, how)`: `how` is `stored`, `asked`, `fake`, or `missing`; owner is a map or page."""

    counts: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    dollars: defaultdict[tuple[str, str, str], float] = field(default_factory=lambda: defaultdict(float))
    """Dollars of the answers used, by the same keys: what a stored answer cost when it was asked."""

    def add(self, owner: str, kind: str, how: str, usd: float = 0.0) -> None:
        """Count one request."""
        with self._lock:
            self.counts[(owner, kind, how)] += 1
            self.dollars[(owner, kind, how)] += usd

    def of(self, owner: str) -> dict[str, dict[str, Any]]:
        """One owner's requests and dollars, by kind and how."""
        out: dict[str, dict[str, Any]] = {}
        for (who, kind, how), count in sorted(self.counts.items()):
            if who == owner:
                entry = out.setdefault(kind, {})
                entry[how] = count
                entry[f"{how} usd"] = round(self.dollars[(who, kind, how)], 6)
        return out


@dataclass
class Sources:
    """Where the run's answers come from.

    Attributes:
        clef_store: The Clef answers by key.
        claude_store: The Claude outcomes by key.
        clef: Asks Clef a request the store lacks, or `None` when the run may not.
        claude: Asks Claude a request the store lacks, or `None` when the run may not.
        clef_how: What `clef`'s answers count as: `asked` for a live backend, `fake` otherwise.
        claude_how: What `claude`'s answers count as.
        tally: Every request, by owner, kind, and how it was answered.
    """

    clef_store: ClefStore
    claude_store: ClaudeStore
    clef: ClefAsk | None = None
    claude: ClaudeAsk | None = None
    clef_how: str = "asked"
    claude_how: str = "asked"
    tally: Tally = field(default_factory=Tally)

    def decide(
        self, request: DecisionRequest, owner: str, item: str, kind: str, about: dict[str, Any] | None = None
    ) -> dict[str, float] | None:
        """Clef's probabilities for a request, from the store or the source.

        Args:
            request: The request.
            owner: The map it is about, for the tally.
            item: The item's name, stored with the answer.
            kind: The kind of question, for the tally.
            about: What the request is about, for a fake source.

        Returns:
            The probabilities by option id, or `None` when the store lacks them and the run may not ask.
        """
        key = request.key()
        stored = self.clef_store.get(key)
        if stored is not None:
            self.tally.add(owner, kind, "stored")
            return stored
        if self.clef is None:
            self.tally.add(owner, kind, "missing")
            return None
        probabilities = self.clef(request, {"map": owner, "item": item, "kind": kind} | (about or {}))
        self.clef_store.put(key, owner, item, probabilities, self.clef_how)
        self.tally.add(owner, kind, self.clef_how)
        return probabilities

    def decide_many(
        self, asks: Sequence[tuple[DecisionRequest, str, str, str, dict[str, Any]]], workers: int = 1
    ) -> list[dict[str, float] | None]:
        """Clef's probabilities for several requests, the ones the store lacks asked on `workers` threads.

        Args:
            asks: Each request with its owner, item, kind, and what it is about, as `decide` takes them.
            workers: How many requests may be in flight at once.

        Returns:
            Each request's probabilities, or `None`, in order.
        """
        if workers <= 1 or self.clef is None:
            return [self.decide(*ask) for ask in asks]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda ask: self.decide(*ask), asks))

    def read(
        self, request: ReadingRequest, check: Callable[[dict[str, Any]], list[str]], owner: str, about: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Claude's outcome for a request, from the store or the source.

        Args:
            request: The request, whose kind names its model.
            check: The kind's check of an answer.
            owner: The map or page the request is about, for the tally.
            about: What the request asks, stored with the outcome.

        Returns:
            The outcome's JSON, or `None` when the store lacks it and the run may not ask.
        """
        kind = request.kind
        if kind.model is None:
            raise ValueError(f"the {kind.name} kind names no model")
        key = request.key(kind.model)
        stored = self.claude_store.get(kind.name, key)
        if stored is not None:
            self.tally.add(owner, kind.name, "stored", float(stored.get("cost_usd") or 0.0))
            return stored
        if self.claude is None:
            self.tally.add(owner, kind.name, "missing")
            return None
        outcome = self.claude(request, check, about)
        record = outcome | {"source": self.claude_how, "about": about}
        self.claude_store.put(kind.name, key, record)
        self.tally.add(owner, kind.name, self.claude_how, float(outcome.get("cost_usd") or 0.0))
        return record
