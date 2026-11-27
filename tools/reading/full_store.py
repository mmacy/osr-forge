"""The full run's answers: stores keyed by each request's key, where an answer comes from, and what each request cost.

Every Clef answer and Claude outcome the run uses is filed under the key of the request that was sent
(`DecisionRequest.key()`, and `ReadingRequest.key(model)` for the kind's model), so an answer is reused only for a
request whose pixels, text, options, model, and parameters are the same:

- [`ClefStore`][tools.reading.full_store.ClefStore] is a JSON-lines file of `{key, map, item, probabilities, source,
  usd}`. A process that dies while it appends leaves a torn last line, which the next load reports and drops.
- [`ClaudeStore`][tools.reading.full_store.ClaudeStore] holds one JSON file per request, `<kind>/<key>.json`, with the
  final outcome (its attempts, answer, and cost) and where it came from, written to a temporary file and renamed into
  place. A request whose attempts ended without a final outcome (a transient failure, the cap, or the ledger's stop)
  keeps those attempts in `<kind>/<key>.pending.json` until a later invocation sends it again. The final outcome then
  lists them as `earlier_attempts`, and its `cost_usd` includes theirs.

The stores are seeded with the screening's and the answer-key pass's stored answers (see `full_seed`). A request the
stores lack goes to the run's [`Sources`][tools.reading.full_store.Sources]: live (local Clef-flash with hosted
Clef-flash behind it, or Claude within the cap), fake (made by a function, for tests and offline runs), or nothing,
when a dry run only counts what it would ask.

The [`Tally`][tools.reading.full_store.Tally] counts each request once, by key, however many maps look it up: the
first lookup names its owner (a map, a page, or a pair) and what it is about, and a later lookup that has an answer
replaces a first one that counted the request missing. Each request has an origin:

- `sent`: asked of a live backend by this run, with the ledger entries it made (a Claude outcome's attempts) and
  what they cost
- `full run`: answered by an earlier invocation of the full run, at what it cost then
- `screening` or `answer-key pass`: reused from the stores those passes left, at what it cost then
- `fake`: made from the answer key, at no cost
- `missing`: neither stored nor asked

A request's attempts that an earlier invocation paid for and that ended without a final outcome are counted apart,
under the origin `full run` with no answer, so this invocation's `sent` figures are what it added to the ledger.

Once a source stops the run (`RunStopped`: a transient failure, the cap, a ledger's stop, or the local Clef server
gone), the sources send nothing more, and every request after it is counted missing, so the run still finishes and
writes its report.
"""

import json
import os
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from osrforge.providers.decisions import DecisionRequest
from osrforge.providers.reading import ReadingRequest

from .claude_run import FINAL_ENDINGS

__all__ = ["ClaudeStore", "ClefAnswer", "ClefStore", "Counted", "RunStopped", "Sources", "Tally", "origin_of"]

ORIGINS = ("sent", "full run", "screening", "answer-key pass", "fake", "missing")
"""Where a request's answer came from, in the order the report lists them."""

_STORED_ORIGIN = {
    "answer-key pass": "answer-key pass",
    "screening": "screening",
    "fake": "fake",
}


class RunStopped(Exception):
    """A source can't go on, so the run sends nothing more: the message says why and what to do."""


def write_atomically(path: Path, text: str) -> None:
    """Write a file whole or not at all: to a temporary file beside it, renamed over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


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


type ClefAsk = Callable[[DecisionRequest, dict[str, Any]], ClefAnswer | Charged | None]
"""Answers a request, or gives `None` when no backend may answer it now, as when the hosted spend is used up, or a
`Charged` when a backend failed after charging for it. The second argument says what the request is about. It
raises `RunStopped` when the run can't go on."""

type ClaudeAsk = Callable[[ReadingRequest, Callable[[dict[str, Any]], list[str]], dict[str, Any]], dict[str, Any]]
"""Answers a request with an outcome's JSON: `attempts`, `answer` (`None` when malformed after the retry), `cost_usd`,
and `ended` (`claude_run.Outcome.ended`) with its `reason`. The third argument says what the request is about."""


@dataclass(frozen=True)
class Charged:
    """A Clef question a backend charged for and didn't answer, with what it charged and why it failed."""

    sent: DecisionRequest
    usd: float
    reason: str
    stop: bool
    """The failure stops the run, as against leaving this one question unanswered."""


class ClefStore:
    """Clef answers by request key, in a JSON-lines file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._answers: dict[str, dict[str, Any]] = {}
        self.torn: str | None = None
        """The torn last line the load dropped, or `None`."""
        if path.exists():
            lines = path.read_text().splitlines()
            for number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    if number < len(lines):
                        raise ValueError(f"{path}: line {number} isn't JSON, and it isn't the last line") from None
                    self.torn = line
                    continue
                self._answers[record["key"]] = record
            if self.torn is not None:
                # A torn line has no newline, so the next answer appended would join it: write the file without it.
                write_atomically(path, "".join(json.dumps(record) + "\n" for record in self._answers.values()))

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
        """Store a final outcome, unless one is stored under the key already, and drop the request's pending
        attempts."""
        path = self._path(kind, key)
        if not path.exists():
            write_atomically(path, json.dumps(record | {"key": key}, indent=1) + "\n")
        self._pending_path(kind, key).unlink(missing_ok=True)

    def _pending_path(self, kind: str, key: str) -> Path:
        return self.root / kind / f"{key}.pending.json"

    def pending(self, kind: str, key: str) -> list[dict[str, Any]]:
        """The attempts earlier invocations made at a request that has no final outcome."""
        path = self._pending_path(kind, key)
        return json.loads(path.read_text())["attempts"] if path.exists() else []

    def add_pending(self, kind: str, key: str, attempts: Sequence[dict[str, Any]], about: dict[str, Any]) -> None:
        """Keep a request's attempts beside the ones kept before, until a final outcome replaces them."""
        record = {"key": key, "about": about, "attempts": [*self.pending(kind, key), *attempts]}
        write_atomically(self._pending_path(kind, key), json.dumps(record, indent=1) + "\n")


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
    answers: int = 1
    """1 for a request with an answer, or one counted missing; 0 for attempts that ended without one."""

    hosted: bool = False
    """A Clef question hosted Clef charged for, which the Clef ledger counts."""


EARLIER = " earlier"
"""What the tally adds to a request's key to count the attempts earlier invocations made at it apart."""

CHARGED = " charged"
"""What the tally adds to a Clef question's key to count a charge for it that brought no answer."""


@dataclass
class Tally:
    """Every request the run looked up, each counted once by its key."""

    entries: dict[str, Counted] = field(default_factory=dict)
    lookups: Counter[tuple[str, str]] = field(default_factory=Counter)
    """How many times each owner looked up each kind, a request looked up twice included."""

    keys: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    """The keys each owner looked up, by kind, whoever counted them first."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, counted: Counted) -> None:
        """Count a request, unless its key is counted already with an answer or an attempt.

        A request counted missing by one owner and then answered or sent for another is counted as the second.
        """
        with self._lock:
            self.lookups[(counted.owner, counted.kind)] += 1
            self.keys.setdefault((counted.owner, counted.kind), set()).add(counted.key)
            counted_before = self.entries.get(counted.key)
            if counted_before is None or (counted_before.origin == "missing" and counted.origin != "missing"):
                self.entries[counted.key] = counted

    def totals(self) -> dict[str, dict[str, Any]]:
        """Requests and dollars by kind and origin."""
        out: dict[str, dict[str, Any]] = {}
        for entry in self.entries.values():
            figures = out.setdefault(f"{entry.kind} {entry.origin}", {"requests": 0, "answers": 0, "usd": 0.0})
            figures["requests"] += entry.requests
            figures["answers"] += entry.answers
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
        claude_owners: The maps and pairs whose item requests and offset picks `claude` may answer, or `None` for
            every owner. Another owner's item request or offset pick is answered from the store or counted missing.
            Whole-page reads aren't held back: every page is read, so every calibration is the one a run of every
            member fits.
        items_held: Why a live `claude` may send no item request, or `None` when it may. The run sets it when a page
            lacks a whole-page outcome or an edge lacks a Clef answer, because an item list fitted on what's missing
            differs from the one the run with them would ask.
        stopped: Why the run stopped sending, or `None` while it sends.
    """

    clef_store: ClefStore
    claude_store: ClaudeStore
    clef: ClefAsk | None = None
    claude: ClaudeAsk | None = None
    claude_how: str = "sent"
    clef_alternates: tuple[str, ...] = ()
    tally: Tally = field(default_factory=Tally)
    claude_owners: set[str] | None = None
    items_held: str | None = None
    stopped: str | None = None
    _stop_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pending_here: set[str] = field(default_factory=set, repr=False)
    """The keys this invocation kept pending attempts for, which aren't an earlier invocation's."""

    def stop(self, reason: str) -> None:
        """Send nothing more, for a reason the report gives; the first reason stands."""
        with self._stop_lock:
            if self.stopped is None:
                self.stopped = reason

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
        missing = Counted(request.key(), owner, kind, "missing", 0, 0.0, about)
        if self.clef is None or self.stopped is not None:
            self.tally.add(missing)
            return None
        try:
            answer = self.clef(request, {"map": owner, "item": item, "kind": kind} | about)
        except RunStopped as stopped:
            self.stop(str(stopped))
            answer = None
        if isinstance(answer, Charged):
            # The ledger counts it, so the tally does too, with no answer; the question itself stays missing.
            charged = Counted(answer.sent.key() + CHARGED, owner, kind, "sent", 1, answer.usd, about, 0, True)
            self.tally.add(charged)
            if answer.stop:
                self.stop(answer.reason)
            answer = None
        if answer is None:
            self.tally.add(missing)
            return None
        key = answer.sent.key()
        self.clef_store.put(key, owner, item, answer.probabilities, answer.source, answer.usd)
        origin = "fake" if answer.source == "fake" else "sent"
        self.tally.add(Counted(key, owner, kind, origin, 1, answer.usd, about, 1, answer.source == "hosted"))
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

        Every attempt that reached the ledger is stored before this returns: a final outcome (answered, malformed
        after the retry, or refused) under the request's key, and the attempts of one that isn't as pending, which
        stops the run.

        Returns:
            The outcome's JSON, or `None` when the store lacks a final outcome and the run may not ask, or the
            request ended without one.
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
        earlier = [] if key in self._pending_here else self.claude_store.pending(kind.name, key)
        if earlier:
            usd = round(sum(float(attempt.get("cost_usd") or 0.0) for attempt in earlier), 6)
            self.tally.add(Counted(key + EARLIER, owner, kind.name, "full run", len(earlier), usd, about, 0))
        if not self._may_send(kind.name, owner):
            self.tally.add(Counted(key, owner, kind.name, "missing", 0, 0.0, about))
            return None
        assert self.claude is not None
        outcome = self.claude(request, check, about)
        origin = "fake" if self.claude_how == "fake" else "sent"
        attempts = outcome.get("attempts") or []
        if outcome.get("ended") not in FINAL_ENDINGS:
            self.claude_store.add_pending(kind.name, key, attempts, about)
            self._pending_here.add(key)
            usd = _usd(outcome)
            self.tally.add(Counted(key, owner, kind.name, origin, len(attempts), usd, about, 0))
            self.stop(f"the {kind.name} request for {owner} ended ({outcome.get('ended')}): {outcome.get('reason')}")
            return None
        record = outcome | {"source": "fake" if self.claude_how == "fake" else "asked", "about": about}
        if earlier:
            record["earlier_attempts"] = earlier
            record["cost_usd"] = round(_usd(outcome) + sum(float(a.get("cost_usd") or 0.0) for a in earlier), 6)
        self.claude_store.put(kind.name, key, record)
        self.tally.add(Counted(key, owner, kind.name, origin, _attempts(outcome), _usd(outcome), about))
        return record

    def _may_send(self, kind: str, owner: str) -> bool:
        """Whether the run may ask its Claude source this request now."""
        if self.claude is None or self.stopped is not None:
            return False
        if kind in HELD_KINDS and self.claude_owners is not None and owner not in self.claude_owners:
            return False
        return not (kind == "reading-items" and self.claude_how == "sent" and self.items_held is not None)


HELD_KINDS = ("reading-items", "reading-offset")
"""The kinds `claude_owners` holds back: whole-page reads go for every page."""


def _attempts(outcome: dict[str, Any]) -> int:
    """The ledger entries an outcome made: one per attempt, earlier invocations' included."""
    return len(outcome.get("attempts") or ()) + len(outcome.get("earlier_attempts") or ())


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
