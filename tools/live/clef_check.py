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

import io
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw

from osrforge.errors import ProviderError
from osrforge.providers.clef import ClefProvider, ClefSettings, cost_usd
from osrforge.providers.decisions import DecisionRequest
from osrforge.providers.ledger import Ledger

MAP = Path(__file__).resolve().parents[2] / "tests" / "assets" / "chaotic-caves" / "maps" / "p038.png"
CROP = 256
RED = (228, 20, 20)

STATE = "Crop of a printed dungeon map. A red box marks one cell edge."
QUESTION = "What is drawn on the cell edge inside the red box?"
OPTIONS = (
    ("open", "No line; the floor continues"),
    ("wall", "A solid wall line"),
    ("door", "A door symbol"),
    ("secret_door", "A secret door symbol, often an S"),
)


def crop() -> bytes:
    """Return a 256 by 256 crop from the middle of the map, with a red box in its middle, as PNG."""
    with Image.open(MAP) as page:
        left, top = (page.width - CROP) // 2, (page.height - CROP) // 2
        image = page.convert("RGB").crop((left, top, left + CROP, top + CROP))
    middle = CROP // 2
    ImageDraw.Draw(image).rectangle((middle - 40, middle - 16, middle + 40, middle + 16), outline=RED, width=3)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def entry_of(ledger: Ledger, first_new: int, label: str) -> dict[str, object] | None:
    """Return the entry this run opened: the one after `first_new` whose `what` is the run's label."""
    entries = ledger.read()["entries"]
    assert isinstance(entries, list)
    for entry in entries[first_new:]:
        if isinstance(entry, dict) and entry.get("what") == label:
            return entry
    return None


def main() -> int:
    """Run the check.

    Returns:
        0 when the request was answered and charged as described in the module docstring, 1 otherwise.
    """
    label = f"osrforge clef_check {datetime.now().isoformat(timespec='seconds')}"
    try:
        settings = ClefSettings.from_env().model_copy(update={"run_label": label})
        ledger = Ledger(settings.ledger_path, cost_key="usd", stop_usd=settings.ledger_stop_usd, count_key="requests")
        entries = ledger.read()["entries"]
        assert isinstance(entries, list)
        first_new = len(entries)
        spent_before = ledger.spent()
    except ProviderError as error:
        print(f"clef check: {error}", file=sys.stderr)
        return 1

    request = DecisionRequest(tag="edge", image=crop(), state=STATE, question=QUESTION, options=OPTIONS)
    failures: list[str] = []
    try:
        response = ClefProvider(settings).decide(request)
    except ProviderError as error:
        print(f"the request failed: {type(error).__name__}: {error}")
        failures.append("the request has no answer")
    else:
        total = sum(response.probabilities.values())
        print(f"answer from {response.model_id}: {response.choice} at {response.confidence:.4f}")
        for option, probability in sorted(response.probabilities.items()):
            print(f"  {option}: {probability:.4f}")
        print(f"image format: {response.format}")
        print(f"input tokens: {response.input_tokens}, cost ${cost_usd(request.model, response.input_tokens):.8f}")
        if abs(total - 1.0) > 0.01:
            failures.append(f"the probabilities sum to {total}, not 1 within 0.01")

    entry = entry_of(ledger, first_new, label)
    print(f"ledger entry written: {entry}")
    print(f"ledger spent_usd: before ${spent_before:.6f}, after ${ledger.spent():.6f}")
    if entry is None:
        failures.append("the ledger has no entry for this run")
    else:
        if entry.get("requests") != 1:
            failures.append(f"the entry's request count rose from 0 to {entry.get('requests')}, not to 1")
        usd = entry.get("usd")
        if not isinstance(usd, int | float) or usd <= 0:
            failures.append(f"the entry's cost rose from 0 to {usd}, not above 0")

    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("PASS: Clef answered the request and the ledger charged it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
