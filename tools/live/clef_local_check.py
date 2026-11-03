"""The local Clef backend's live check: one request to a System One server on this machine.

This is the one check of the local backend that calls a server, and it never
runs under `pytest`. It costs nothing and writes no ledger.

Usage, from the repository root with a server running that has Clef-flash
pulled, such as Ollama (`OSRFORGE_CLEF_LOCAL_URL` and
`OSRFORGE_CLEF_LOCAL_MODEL` name another server or model):

    uv run python tools/live/clef_local_check.py

It sends the same crop and question as `tools/live/clef_check.py`: a 256 by
256 pixel crop from the committed Chaotic Caves map with a red box in its
middle, and the options open, wall, door, and secret door. It prints the
probabilities, the model id, the format the image went as, and the input
tokens. It exits 0 when the probabilities sum to 1 within 0.01. Otherwise it
prints what differed and exits 1. The check never starts or stops the server.
"""

import sys

from clef_check import OPTIONS, QUESTION, STATE, crop

from osrforge.errors import ProviderError
from osrforge.providers.clef_local import LocalClefProvider, LocalClefSettings
from osrforge.providers.decisions import DecisionRequest


def main() -> int:
    """Run the check.

    Returns:
        0 when the server answered and the probabilities sum to 1 within 0.01, 1 otherwise.
    """
    try:
        settings = LocalClefSettings.from_env()
        print(f"server: {settings.base_url}")
        request = DecisionRequest(
            tag="edge", image=crop(), state=STATE, question=QUESTION, options=OPTIONS, model="clef-flash"
        )
        response = LocalClefProvider(settings).decide(request)
    except ProviderError as error:
        print(f"the request failed: {type(error).__name__}: {error}")
        return 1
    print(f"answer: {response.choice} at {response.confidence:.4f}")
    for option, probability in sorted(response.probabilities.items()):
        print(f"  {option}: {probability:.4f}")
    print(f"model id: {response.model_id}")
    print(f"image format: {response.format}")
    print(f"input tokens: {response.input_tokens}")
    total = sum(response.probabilities.values())
    if abs(total - 1.0) > 0.01:
        print(f"FAIL: the probabilities sum to {total}, not 1 within 0.01")
        return 1
    print("PASS: the local server answered the request")
    return 0


if __name__ == "__main__":
    sys.exit(main())
