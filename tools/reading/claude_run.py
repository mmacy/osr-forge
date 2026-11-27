"""Claude requests for the screening: the provider, the one retry of a malformed answer, the cap, and the store.

Every request goes through [`ClaudeProvider`][osrforge.providers.claude.ClaudeProvider], the stage's provider, so it
streams, sends the stage's body, and reserves its worst case on the Claude ledger before sending and settles it
after. A request goes to its kind's model, or to the provider's default model when the kind names none, and its
outcome is charged at that model's prices. The API key is read from the `ANTHROPIC_API_KEY=` line of
`~/repos/re-pod/.env` inside the process and never printed. The ledger is the one `OSRFORGE_CLAUDE_LEDGER` names.

An answer is malformed when the provider can't read it (it stopped at `max_tokens`, was refused, or isn't JSON that
fits the schema) or when the kind's own check finds faults. A malformed answer is asked once more, as the stage
does: the original request with the answer and its faults appended when there is an answer to append, and the
original request again when there isn't. Both attempts are recorded, each with what the ledger charged for it, read
back from the ledger entry the provider settled: requests go one at a time, so the entry is the last one of the
request's label and model. An answer the provider can't read was settled at its usage before the provider raised, so
the ledger, not the exception, holds what it cost. Without a ledger entry, as with a stand-in provider, an answer is
charged at its usage's price.

The screening's requests are capped: before each request, the runner counts the Claude ledger's entries whose label
starts with `reading-` (every kind here is named so) and refuses to send past the cap.

[`ask`][tools.reading.claude_run.ask] never raises for a request that went wrong. It returns an outcome that holds
every attempt that reached the ledger, and says how the request ended (`Outcome.ended`):

- `None`: answered, or malformed after the retry. The outcome is final.
- `refused`: the API refused an attempt with a status that names a fault in the request itself (400, 404, 413, or
  422). Sending it again would be refused again, so the outcome is final, with no answer.
- `failed`: the attempts ended in a failure that may pass: a 5xx, a lost connection, 429s past the provider's
  retries, a 408 or 409, or a 401 or 403, which name the key or the account rather than the request. The outcome
  isn't final.
- `cap` or `ledger stop`: the cap or the ledger's stop refused the next attempt before it was sent. The outcome isn't
  final, and holds the attempts made before.

The caller stores the attempts of an outcome that isn't final, stops sending, and sends the request again on a later
invocation.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import SecretStr

from osrforge.errors import LedgerStopError, ProviderError, ReadingFailure, SchemaValidationError
from osrforge.providers.claude import ClaudeProvider, ClaudeSettings, cost_usd
from osrforge.providers.reading import ReadingRequest, ReadingResponse, Retry

__all__ = [
    "FINAL_ENDINGS",
    "REFUSED_STATUSES",
    "Attempt",
    "Outcome",
    "RequestCap",
    "ask",
    "load_api_key",
    "make_provider",
    "settled_since",
    "stop_unless_final",
]

ENV_FILE = Path("~/repos/re-pod/.env").expanduser()
LABEL_PREFIX = "reading-"

REFUSED_STATUSES = frozenset({400, 404, 413, 422})
"""The statuses that refuse a request for a fault in the request itself, so sending it again is refused again. A 401
or 403 names the key or the account, and a 429 the rate, so a request refused with them may be sent again."""

FINAL_ENDINGS = (None, "refused")
"""The endings of an outcome that is final: answered or malformed after the retry, and refused."""


def load_api_key(path: Path = ENV_FILE) -> SecretStr:
    """The API key from the `ANTHROPIC_API_KEY=` line of an env file."""
    for line in path.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return SecretStr(line.split("=", 1)[1].strip().strip('"').strip("'"))
    raise SystemExit(f"no ANTHROPIC_API_KEY line in {path}")


def make_provider(model: str) -> ClaudeProvider:
    """The stage's provider on a model, charged to the ledger `OSRFORGE_CLAUDE_LEDGER` names."""
    ledger = os.environ.get("OSRFORGE_CLAUDE_LEDGER", "").strip()
    if not ledger:
        raise SystemExit("set OSRFORGE_CLAUDE_LEDGER")
    return ClaudeProvider(ClaudeSettings(api_key=load_api_key(), ledger_path=Path(ledger).expanduser(), model=model))


@dataclass
class RequestCap:
    """Refuses a request once the ledger holds `cap` entries of the screening's kinds."""

    ledger: Path
    cap: int

    def sent(self) -> int:
        """The screening's requests on the ledger so far."""
        entries = json.loads(self.ledger.read_text())["entries"]
        return sum(str(entry.get("label", "")).startswith(LABEL_PREFIX) for entry in entries)

    def reached(self) -> str | None:
        """Why the next request would pass the cap, or `None` when it wouldn't."""
        sent = self.sent()
        return f"the cap of {self.cap} Claude requests is reached ({sent} sent)" if sent >= self.cap else None

    def check(self) -> None:
        """Raise when the next request would pass the cap."""
        reached = self.reached()
        if reached is not None:
            raise SystemExit(reached)


@dataclass
class Attempt:
    """One request and what came back."""

    retry: bool
    seconds: float
    usage: dict[str, int] | None = None
    cost_usd: float | None = None
    answer: dict[str, Any] | None = None
    error: str | None = None
    faults: list[str] = field(default_factory=list)
    status: int | None = None
    """The HTTP status that ended a failed attempt, when the API gave one."""


@dataclass
class Outcome:
    """A request's attempts and its final answer, or none when the retry was malformed too or the request ended."""

    model: str
    effort: str
    attempts: list[Attempt]
    ended: str | None = None
    """How the request ended, when it ended without an answer to check: `refused`, `failed`, `cap`, or
    `ledger stop`. `None` for an answer, well formed or not."""

    reason: str | None = None
    """What ended it, in words."""

    @property
    def final(self) -> bool:
        """Sending the request again can't change its outcome."""
        return self.ended in FINAL_ENDINGS

    @property
    def answer(self) -> dict[str, Any] | None:
        """The last attempt's answer when it was well formed and the request didn't end otherwise."""
        if self.ended is not None or not self.attempts:
            return None
        last = self.attempts[-1]
        return last.answer if last.answer is not None and not last.faults else None

    @property
    def malformed_first(self) -> bool:
        """The first answer was malformed."""
        if not self.attempts:
            return False
        first = self.attempts[0]
        return first.answer is None or bool(first.faults)

    def to_json(self) -> dict[str, Any]:
        """The outcome as JSON."""
        return {
            "model": self.model,
            "effort": self.effort,
            "attempts": [attempt.__dict__ for attempt in self.attempts],
            "answer": self.answer,
            "malformed_first": self.malformed_first,
            "cost_usd": round(sum(attempt.cost_usd or 0.0 for attempt in self.attempts), 6),
            "ended": self.ended,
            "reason": self.reason,
        }


def _entries(ledger: Path) -> list[dict[str, Any]]:
    return json.loads(ledger.read_text())["entries"] if ledger.exists() else []


def settled_since(ledger: Path, before: int, label: str, model: str) -> float | None:
    """What the ledger's last entry for a request, made after `before` entries, was settled at.

    The provider settles a request that ended with a malformed answer (a refusal, a stop at `max_tokens`, or JSON the
    schema refuses) at the answer's usage before it raises, so the ledger, not the exception, holds what it cost.

    Args:
        ledger: The Claude ledger.
        before: How many entries the ledger held before the request.
        label: The entry's label, `<kind> <effort>`.
        model: The entry's model.

    Returns:
        The cost of the last matching entry made since, or `None` when there is none.
    """
    for entry in reversed(_entries(ledger)[before:]):
        if entry.get("label") == label and entry.get("model") == model:
            return float(entry.get("cost_usd") or 0.0)
    return None


def _status(error: BaseException) -> int | None:
    """The HTTP status of the API error behind a provider's error, when there is one."""
    status = getattr(error.__cause__, "status_code", None)
    return status if isinstance(status, int) else None


def _one(
    provider: ClaudeProvider, request: ReadingRequest, check: Callable[[dict[str, Any]], list[str]], retry: bool
) -> tuple[Attempt | None, str | None, str | None]:
    """Send one attempt.

    Returns:
        The attempt, or `None` when nothing reached the ledger; how the request ended (`refused`, `failed`, or
        `ledger stop`), or `None` when it has an answer to check or a malformed one to retry; and what ended it.
    """
    started = time.monotonic()
    model = request.kind.model or provider.settings.model
    ledger = provider.settings.ledger_path
    label = f"{request.kind.name} {request.kind.effort}"
    before = len(_entries(ledger))
    try:
        response: ReadingResponse = provider.read(request)
    except (ReadingFailure, SchemaValidationError) as error:
        attempt = Attempt(
            retry=retry,
            seconds=round(time.monotonic() - started, 1),
            cost_usd=settled_since(ledger, before, label, model),
            error=str(error),
        )
        return attempt, None, None
    except LedgerStopError as error:
        return None, "ledger stop", str(error)
    except ProviderError as error:
        status = _status(error)
        settled = settled_since(ledger, before, label, model)
        ended = "refused" if status in REFUSED_STATUSES else "failed"
        if settled is None:
            # Nothing reached the ledger, so there is no attempt to record.
            return None, ended, str(error)
        attempt = Attempt(
            retry=retry,
            seconds=round(time.monotonic() - started, 1),
            cost_usd=settled,
            error=str(error),
            status=status,
        )
        return attempt, ended, str(error)
    usage = response.usage
    settled = settled_since(ledger, before, label, model)
    attempt = Attempt(
        retry=retry,
        seconds=round(time.monotonic() - started, 1),
        usage=usage.model_dump(),
        cost_usd=cost_usd(model, usage) if settled is None else settled,
        answer=cast(dict[str, Any], response.data),
        faults=check(cast(dict[str, Any], response.data)),
    )
    return attempt, None, None


def ask(
    provider: ClaudeProvider,
    request: ReadingRequest,
    check: Callable[[dict[str, Any]], list[str]],
    cap: RequestCap,
) -> Outcome:
    """Send a request, and once more when its answer is malformed.

    Nothing that goes wrong with the request raises: the outcome holds every attempt that reached the ledger and says
    how the request ended.

    Args:
        provider: The provider.
        request: The request.
        check: The kind's check of what the schema can't catch. It returns the faults.
        cap: The request cap, checked before each attempt.

    Returns:
        The outcome.
    """
    model = request.kind.model or provider.settings.model
    attempts: list[Attempt] = []

    def outcome(ended: str | None = None, reason: str | None = None) -> Outcome:
        return Outcome(model=model, effort=request.kind.effort, attempts=attempts, ended=ended, reason=reason)

    reached = cap.reached()
    if reached is not None:
        return outcome("cap", reached)
    first, ended, reason = _one(provider, request, check, retry=False)
    if first is not None:
        attempts.append(first)
    if ended is not None or first is None:
        return outcome(ended, reason)
    if first.answer is None or first.faults:
        reached = cap.reached()
        if reached is not None:
            return outcome("cap", reached)
        if first.answer is not None:
            fault = "The answer is malformed:\n- " + "\n- ".join(first.faults) + "\nAnswer again in full."
            retry = ReadingRequest(
                kind=request.kind,
                shared=request.shared,
                question=request.question,
                retry=Retry(answer=json.dumps(first.answer), fault=fault),
            )
        else:
            retry = request
        second, ended, reason = _one(provider, retry, check, retry=True)
        if second is not None:
            attempts.append(second)
        if ended is not None:
            return outcome(ended, reason)
    return outcome()


def stop_unless_final(outcome: Outcome) -> None:
    """Stop a screening driver at a request that didn't finish, naming what its attempts cost.

    The screening drivers store final outcomes only, so a request that ended on a failure, the cap, or the ledger's
    stop is sent again by the next invocation, and its attempts so far are paid for again.

    Raises:
        SystemExit: If the outcome isn't final.
    """
    if not outcome.final:
        spent = round(sum(attempt.cost_usd or 0.0 for attempt in outcome.attempts), 6)
        raise SystemExit(
            f"the request ended ({outcome.ended}: {outcome.reason}) after {len(outcome.attempts)} attempts that "
            f"cost ${spent}"
        )
