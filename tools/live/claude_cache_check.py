"""The Claude provider's live check: two Sonnet requests that share a cached part.

This is the one check of the Claude provider that calls the service, and it
never runs under `pytest`. It spends about two cents of the maintainer's API
credits and charges them to the Claude ledger.

Usage, from the repository root with `ANTHROPIC_API_KEY` and
`OSRFORGE_CLAUDE_LEDGER` set:

    uv run python tools/live/claude_cache_check.py

It builds one request kind at low effort with the schema of a short answer,
whose system prompt starts with a value new to this run (the time), so
nothing in the cached prefix can be warm from an earlier run, and two
requests whose one shared part is the same text, longer than 512 tokens. It
sends both through
`ClaudeProvider.read_all` on `claude-sonnet-5-5`, and it prints each answer's
usage and the ledger entries it wrote. It exits 0 when both answers fit the
schema, the first answer's cache write is greater than zero, and the second
answer's cache read equals the first's cache write. Otherwise it prints what
differed and exits 1.
"""

import sys
from datetime import datetime

from osrforge.errors import ProviderError
from osrforge.providers.base import TextPart
from osrforge.providers.claude import ClaudeProvider, ClaudeSettings, cost_usd
from osrforge.providers.ledger import Ledger
from osrforge.providers.reading import ReadingRequest, ReadingResponse, RequestKind

MODEL = "claude-sonnet-5-5"
LABEL = "cache_check low"

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

SHAPES = ("a hollow square", "a filled square", "a hollow circle", "a filled circle", "a diamond")
MARKS = ("with a dot in the centre", "with a bar across it", "inside a larger outline", "with a tail on its left")
MEANINGS = (
    "a wooden door",
    "a locked door",
    "a secret door",
    "a portcullis",
    "a pit trap",
    "a staircase going up",
    "a staircase going down",
    "a ladder",
    "a fountain",
    "a statue",
    "an altar",
    "a pillar",
    "a well",
    "a chest",
    "a throne",
    "a fireplace",
    "rubble",
    "a pool of water",
    "a rope bridge",
    "a chasm",
)

QUESTIONS = (
    "Which symbol number marks a pit trap? Answer with the number alone.",
    "Which symbol number marks a secret door? Answer with the number alone.",
)


def legend() -> str:
    """Return the shared part: a legend of forty map symbols, well over the cache's 512-token minimum."""
    lines = ["The legend of the map. Each symbol has a number, a drawing, and a meaning."]
    for number in range(1, 41):
        shape = SHAPES[(number - 1) % len(SHAPES)]
        mark = MARKS[(number - 1) // len(SHAPES) % len(MARKS)]
        meaning = MEANINGS[number - 1] if number <= len(MEANINGS) else f"room feature {number} of the second level"
        lines.append(
            f"Symbol {number}: {shape} {mark} marks {meaning}. Where it appears on the map, the room that holds it "
            f"is described in the key under the room's own number."
        )
    return "\n".join(lines)


def entries_of(ledger: Ledger) -> list[object]:
    """Return the ledger's entries."""
    entries = ledger.read()["entries"]
    assert isinstance(entries, list)
    return entries


def main() -> int:
    """Run the check.

    Returns:
        0 when the cache was shared as described in the module docstring, 1 otherwise.
    """
    try:
        settings = ClaudeSettings.from_env().model_copy(update={"model": MODEL})
        ledger = Ledger(settings.ledger_path, cost_key="cost_usd", stop_usd=settings.ledger_stop_usd)
        first_new = len(entries_of(ledger))
        spent_before = ledger.spent()
    except ProviderError as error:
        print(f"claude cache check: {error}", file=sys.stderr)
        return 1

    kind = RequestKind(
        name="cache_check",
        system=f"This check started at {datetime.now().isoformat()}. You answer questions about a map legend.",
        schema=SCHEMA,
        effort="low",
        max_tokens=2000,
    )
    shared = ((TextPart(text=legend()),),)
    requests = [ReadingRequest(kind=kind, shared=shared, question=(TextPart(text=text),)) for text in QUESTIONS]
    results = ClaudeProvider(settings).read_all(requests)

    failures: list[str] = []
    answers: list[ReadingResponse] = []
    for number, (question, result) in enumerate(zip(QUESTIONS, results, strict=True), start=1):
        print(f"request {number}: {question}")
        if not isinstance(result, ReadingResponse):
            print(f"  failed: {type(result).__name__}: {result}")
            failures.append(f"request {number} has no answer that fits the schema")
            continue
        answers.append(result)
        usage = result.usage
        print(f"  answer: {result.data}, from {result.model_id}")
        print(
            f"  usage: input {usage.input_tokens}, output {usage.output_tokens}, "
            f"cache write {usage.cache_write_tokens}, cache read {usage.cache_read_tokens}, "
            f"cost ${cost_usd(MODEL, usage):.6f}"
        )

    print("ledger entries written:")
    for entry in entries_of(ledger)[first_new:]:
        if isinstance(entry, dict) and entry.get("script") == "osrforge" and entry.get("label") == LABEL:
            print(f"  {entry}")
    print(f"ledger spent_usd: before ${spent_before:.6f}, after ${ledger.spent():.6f}")

    if len(answers) == len(QUESTIONS):
        first, second = answers[0].usage, answers[1].usage
        if first.cache_write_tokens <= 0:
            failures.append("the first answer wrote nothing to the cache")
        if second.cache_read_tokens != first.cache_write_tokens:
            failures.append(
                f"the second answer read {second.cache_read_tokens} cached tokens, "
                f"but the first wrote {first.cache_write_tokens}"
            )
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("PASS: the second request read the cache entry the first one wrote")
    return 0


if __name__ == "__main__":
    sys.exit(main())
