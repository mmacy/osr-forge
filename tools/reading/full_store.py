"""The full run's answers: stores keyed by each request's key, where an answer comes from, and what each request cost.

Every Clef answer and Claude outcome the run uses is filed under the key of the request that was sent
(`DecisionRequest.key()`, and `ReadingRequest.key(model)` for the kind's model), so an answer is reused only for a
request whose pixels, text, options, model, and parameters are the same:

- [`ClefStore`][tools.reading.full_store.ClefStore] is a JSON-lines file of `{key, map, item, probabilities, source,
  usd}`.
- [`ClaudeStore`][tools.reading.full_store.ClaudeStore] holds one JSON file per request, `<kind>/<key>.json`, with the
  outcome (its attempts, answer, and cost) and where it came from.

The stores are seeded with the screening's and the answer-key pass's stored answers (see `full_seed`). A request the
stores lack goes to the run's [`Sources`][tools.reading.full_store.Sources]: live (local Clef-flash with hosted
Clef-flash behind it, or Claude within the cap), fake (made by a function, for tests and offline runs), or nothing,
when a dry run only counts what it would ask.

The [`Tally`][tools.reading.full_store.Tally] counts each request once, by key, however many maps look it up: the
first lookup names its owner (a map, a page, or a pair) and what it is about. Each request has an origin:

- `sent`: asked of a live backend by this run, with the ledger entries it made (a Claude outcome's attempts) and
  what they cost
- `full run`: answered by an earlier invocation of the full run, at what it cost then
- `screening` or `answer-key pass`: reused from the stores those passes left, at what it cost then
- `fake`: made from the answer key, at no cost
- `missing`: neither stored nor asked
"""

import json
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.providers.decisions import DecisionRequest
from osrforge.providers.reading import ReadingRequest

__all__ = ["ClaudeStore", "ClefAnswer", "ClefStore", "Counted", "Sources", "Tally", "origin_of"]

ORIGINS = ("sent", "full run", "screening", "answer-key pass", "fake", "missing")
"""Where a request's answer came from, in the order the report lists them."""

_STORED_ORIGIN = {
    "answer-key pass": "answer-key pass",
    "screening": "screening",
    "fake": "fake",
}


def origin_of(source: str | None) -> str:
    """The origin of a stored answer, from the source its record names: any live backend's is `full run`."""
    return _STORED_ORIGIN.get(source or "", "full run")


@dataclass(frozen=True)
class ClefAnswer:
    """An answer from a Clef source, with the request that was sent for it."""

    probabilities: dict[str, float]
    sent: DecisionRequest
    """The request actually sent: the asked one, or the same request named for the fallback's model."""

    source: str
    """`local`, `hosted`, or `fake`."""

    usd: float = 0.0
    """What the backend charged for it."""


type ClefAsk = Callable[[DecisionRequest, dict[str, Any]], ClefAnswer]
"""Answers a request. The second argument says what the request is about."""

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

    def record(self, key: str) -> dict[str, Any] | None:
        """The whole stored record for a request key, or `None`."""
        return self._answers.get(key)

    def source(self, key: str) -> str | None:
        """Where a stored answer came from."""
        record = self._answers.get(key)
        return None if record is None else record.get("source")

    def put(
        self, key: str, map_uid: str, item: str, probabilities: dict[str, float], source: str, usd: float = 0.0
    ) -> None:
        """Store an answer, unless one is stored under the key already."""
        with self._lock:
            if key in self._answers:
                return
            record = {
                "key": key,
                "map": map_uid,
                "item": item,
                "probabilities": probabilities,
                "source": source,
                "usd": usd,
            }
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


@dataclass(frozen=True)
class Counted:
    """One request, counted once."""

    key: str
    owner: str
    kind: str
    origin: str
    requests: int
    """Ledger entries: a Claude outcome's attempts, 1 for a Clef answer, 0 for a missing one."""

    usd: float
    about: dict[str, Any]


@dataclass
class Tally:
    """Every request the run looked up, each counted once by its key."""

    entries: dict[str, Counted] = field(default_factory=dict)
    lookups: Counter[tuple[str, str]] = field(default_factory=Counter)
    """How many times each owner looked up each kind, a request looked up twice included."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, counted: Counted) -> None:
        """Count a request, unless its key is counted already."""
        with self._lock:
            self.lookups[(counted.owner, counted.kind)] += 1
            self.entries.setdefault(counted.key, counted)

    def totals(self) -> dict[str, dict[str, Any]]:
        """Requests and dollars by kind and origin."""
        out: dict[str, dict[str, Any]] = {}
        for entry in self.entries.values():
            figures = out.setdefault(f"{entry.kind} {entry.origin}", {"requests": 0, "answers": 0, "usd": 0.0})
            figures["requests"] += entry.requests
            figures["answers"] += 1
            figures["usd"] = round(figures["usd"] + entry.usd, 6)
        return dict(sorted(out.items()))

    def of(self, owner: str) -> list[Counted]:
        """One owner's requests."""
        return [entry for entry in self.entries.values() if entry.owner == owner]


@dataclass
class Sources:
    """Where the run's answers come from.

    Attributes:
        clef_store: The Clef answers by key.
        claude_store: The Claude outcomes by key.
        clef: Asks Clef a request the store lacks, or `None` when the run may not.
        claude: Asks Claude a request the store lacks, or `None` when the run may not.
        claude_how: What `claude`'s outcomes count as: `sent` for a live backend, `fake` otherwise.
        clef_alternates: The other models a Clef request may have been sent under, so a fallback's answer filed under
            its own request's key is found again.
        tally: Every request, counted once.
        claude_owners: The owners (maps, pages, and pairs) `claude` may answer for, or `None` for every owner. A
            request about another owner is answered from the store or counted missing.
    """

    clef_store: ClefStore
    claude_store: ClaudeStore
    clef: ClefAsk | None = None
    claude: ClaudeAsk | None = None
    claude_how: str = "sent"
    clef_alternates: tuple[str, ...] = ()
    tally: Tally = field(default_factory=Tally)
    claude_owners: set[str] | None = None

    def decide(
        self, request: DecisionRequest, owner: str, item: str, kind: str, about: dict[str, Any] | None = None
    ) -> dict[str, float] | None:
        """Clef's probabilities for a request, from the store or the source.

        Args:
            request: The request.
            owner: The map it is about, for the tally.
            item: The item's name, stored with the answer.
            kind: The kind of question, for the tally.
            about: What the request is about: a fake source reads it, and the report places the request by it.

        Returns:
            The probabilities by option id, or `None` when the store lacks them and the run may not ask.
        """
        about = about or {}
        candidates = [
            request,
            *(_for_model(request, model) for model in self.clef_alternates if model != request.model),
        ]
        for candidate in candidates:
            key = candidate.key()
            record = self.clef_store.record(key)
            if record is not None:
                origin = origin_of(record.get("source"))
                self.tally.add(Counted(key, owner, kind, origin, 1, float(record.get("usd") or 0.0), about))
                return record["probabilities"]
        if self.clef is None:
            self.tally.add(Counted(request.key(), owner, kind, "missing", 0, 0.0, about))
            return None
        answer = self.clef(request, {"map": owner, "item": item, "kind": kind} | about)
        key = answer.sent.key()
        self.clef_store.put(key, owner, item, answer.probabilities, answer.source, answer.usd)
        origin = "fake" if answer.source == "fake" else "sent"
        self.tally.add(Counted(key, owner, kind, origin, 1, answer.usd, about))
        return answer.probabilities

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
            owner: The map, page, or pair the request is about, for the tally.
            about: What the request asks, stored with the outcome and used to place it in the report.

        Returns:
            The outcome's JSON, or `None` when the store lacks it and the run may not ask.
        """
        kind = request.kind
        if kind.model is None:
            raise ValueError(f"the {kind.name} kind names no model")
        key = request.key(kind.model)
        stored = self.claude_store.get(kind.name, key)
        if stored is not None:
            origin = origin_of(stored.get("source"))
            self.tally.add(Counted(key, owner, kind.name, origin, _attempts(stored), _usd(stored), about))
            return stored
        if self.claude is None or (self.claude_owners is not None and owner not in self.claude_owners):
            self.tally.add(Counted(key, owner, kind.name, "missing", 0, 0.0, about))
            return None
        outcome = self.claude(request, check, about)
        record = outcome | {"source": "fake" if self.claude_how == "fake" else "asked", "about": about}
        self.claude_store.put(kind.name, key, record)
        origin = "fake" if self.claude_how == "fake" else "sent"
        self.tally.add(Counted(key, owner, kind.name, origin, _attempts(outcome), _usd(outcome), about))
        return record


def _attempts(outcome: dict[str, Any]) -> int:
    """The ledger entries an outcome made: one per attempt."""
    return max(1, len(outcome.get("attempts") or ()))


def _usd(outcome: dict[str, Any]) -> float:
    return float(outcome.get("cost_usd") or 0.0)


def _for_model(request: DecisionRequest, model: str) -> DecisionRequest:
    return DecisionRequest(
        tag=request.tag,
        image=request.image,
        state=request.state,
        question=request.question,
        options=request.options,
        model=model,  # type: ignore[arg-type]
    )
