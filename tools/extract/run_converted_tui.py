"""The milestone substitution: osrlib's example TUI crawler over a converted `adventure.json`.

The examples ship in the osrlib repo, not the wheel, so this needs the
checkout (default `~/repos/osrlib-python`). The swap is exactly the phase 2
milestone's: the example's `build_adventure()` is replaced with loading the
converted document through `check_document` + `Adventure.model_validate`.
Interactive by default; pass the TUI's own `--script transcript.txt` for a
reproducible session.

Usage:
    uv run tools/extract/run_converted_tui.py <workdir>/adventure.json [--seed N] [--script FILE]
"""

import json
import sys
from pathlib import Path

OSRLIB_CHECKOUT = Path.home() / "repos" / "osrlib-python"


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.path.insert(0, str(OSRLIB_CHECKOUT))
    try:
        import examples.tui_crawler.__main__ as tui
    except ImportError:
        sys.exit(f"the osrlib examples are not importable from {OSRLIB_CHECKOUT} — clone osrlib-python there")

    from osrlib.crawl.adventure import Adventure
    from osrlib.versioning import check_document

    document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    adventure = Adventure.model_validate(check_document(document, "adventure"))
    tui.build_adventure = lambda: adventure
    return tui.main(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
