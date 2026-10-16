"""The Claude provider's live check: two Sonnet requests that share a cached part.

This is the one check of the Claude provider that calls the service, and it
never runs under `pytest`. It spends about two cents of the maintainer's API
credits and charges them to the Claude ledger.

Usage, from the repository root with `ANTHROPIC_API_KEY` and
`OSRFORGE_CLAUDE_LEDGER` set:

    uv run python tools/live/claude_cache_check.py

It builds one request kind at low effort with the schema of a short answer,
and two requests whose one shared part is the same text, longer than 512
tokens, that starts with a value new to this run (the time, so no earlier
run's cache entry can serve it). It sends both through
`ClaudeProvider.read_all` on `claude-sonnet-5-5`, and it prints each answer's
usage and the ledger entries it wrote. It exits 0 when both answers fit the
schema, the first answer's cache write is greater than zero, and the second
answer's cache read equals the first's cache write. Otherwise it prints what
differed and exits 1.
"""

import sys


def main() -> int:
    """Run the check.

    Returns:
        0 when the cache was shared as described in the module docstring, 1 otherwise.
    """
    raise NotImplementedError("chunk: claude-provider")


if __name__ == "__main__":
    sys.exit(main())
