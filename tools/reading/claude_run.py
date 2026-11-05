"""Claude requests for the screening: the provider, the one retry of a malformed answer, the cap, and the store.

Every request goes through [`ClaudeProvider`][osrforge.providers.claude.ClaudeProvider], the stage's provider, so it
streams, sends the stage's body, and reserves its worst case on the Claude ledger before sending and settles it
after. A request goes to its kind's model, or to the provider's default model when the kind names none, and its
outcome is charged at that model's prices. The API key is read from the `ANTHROPIC_API_KEY=` line of
`~/repos/re-pod/.env` inside the process and never printed. The ledger is the one `OSRFORGE_CLAUDE_LEDGER` names.

An answer is malformed when the provider can't read it (it stopped at `max_tokens`, was refused, or isn't JSON that
fits the schema) or when the kind's own check finds faults. A malformed answer is asked once more, as the stage
does: the original request with the answer and its faults appended when there is an answer to append, and the
original request again when there isn't. Both attempts are recorded.

The screening's requests are capped: before each request, the runner counts the Claude ledger's entries whose label
starts with `reading-` (every kind here is named so) and refuses to send past the cap.
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from pydantic import SecretStr

from osrforge.errors import ReadingFailure, SchemaValidationError
from osrforge.providers.claude import ClaudeProvider, ClaudeSettings, cost_usd
from osrforge.providers.reading import ReadingRequest, ReadingResponse, Retry

__all__ = ["Attempt", "Outcome", "RequestCap", "ask", "load_api_key", "make_provider"]

ENV_FILE = Path("~/repos/re-pod/.env").expanduser()
LABEL_PREFIX = "reading-"


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

    def check(self) -> None:
        """Raise when the next request would pass the cap."""
        sent = self.sent()
        if sent >= self.cap:
            raise SystemExit(f"the screening's cap of {self.cap} Claude requests is reached ({sent} sent)")


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


@dataclass
class Outcome:
    """A request's attempts and its final answer, or none when the retry was malformed too."""

    model: str
    effort: str
    attempts: list[Attempt]

    @property
    def answer(self) -> dict[str, Any] | None:
        """The last attempt's answer when it was well formed."""
        last = self.attempts[-1]
        return last.answer if last.answer is not None and not last.faults else None

    @property
    def malformed_first(self) -> bool:
        """The first answer was malformed."""
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
        }


def _one(
    provider: ClaudeProvider, request: ReadingRequest, check: Callable[[dict[str, Any]], list[str]], retry: bool
) -> Attempt:
    started = time.monotonic()
    try:
        response: ReadingResponse = provider.read(request)
    except (ReadingFailure, SchemaValidationError) as error:
        return Attempt(retry=retry, seconds=round(time.monotonic() - started, 1), error=str(error))
    usage = response.usage
    return Attempt(
        retry=retry,
        seconds=round(time.monotonic() - started, 1),
        usage=usage.model_dump(),
        cost_usd=cost_usd(request.kind.model or provider.settings.model, usage),
        answer=cast(dict[str, Any], response.data),
        faults=check(cast(dict[str, Any], response.data)),
    )


def ask(
    provider: ClaudeProvider,
    request: ReadingRequest,
    check: Callable[[dict[str, Any]], list[str]],
    cap: RequestCap,
) -> Outcome:
    """Send a request, and once more when its answer is malformed.

    Args:
        provider: The provider.
        request: The request.
        check: The kind's check of what the schema can't catch. It returns the faults.
        cap: The screening's request cap, checked before each request.

    Returns:
        The outcome.
    """
    cap.check()
    first = _one(provider, request, check, retry=False)
    attempts = [first]
    if first.answer is None or first.faults:
        cap.check()
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
        attempts.append(_one(provider, retry, check, retry=True))
    return Outcome(model=request.kind.model or provider.settings.model, effort=request.kind.effort, attempts=attempts)
