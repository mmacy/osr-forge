"""The Clef provider's live check: one request to Clef on Workers AI.

This is the one check of the Clef provider that calls the service, and it
never runs under `pytest`. It spends well under a cent and charges it to
the Clef ledger.

Usage, from the repository root with `OSRFORGE_CLEF_ACCOUNT_ID` and
`OSRFORGE_CLEF_LEDGER` set, and either `OSRFORGE_CLEF_TOKEN` set or a
wrangler login:

    uv run python tools/live/clef_check.py

It cuts a 256 by 256 pixel crop from the committed Chaotic Caves map
`tests/assets/chaotic-caves/maps/p038.png`, draws a red box in the middle
of it, and asks Clef what is drawn inside the box, with the options open,
wall, door, and secret door. It prints the probabilities, the format the
image went as, the input tokens, and the ledger entry it wrote. It exits 0
when the probabilities sum to 1 within 0.01, the ledger entry's request
count rose by one, and its cost rose by more than zero. Otherwise it prints
what differed and exits 1.
"""

import sys


def main() -> int:
    """Run the check.

    Returns:
        0 when the request was answered and charged as described in the module docstring, 1 otherwise.
    """
    raise NotImplementedError("chunk: clef-provider")


if __name__ == "__main__":
    sys.exit(main())
