"""The sample app: a local page where the owner checks an answer key's settled calls, one key press each.

Run it from the osr-forge checkout with a sample file the answer-key pipeline wrote:

```text
uv run python -m tools.answer_keys.sample_app <sample.json> [--port 8765]
```

It serves on `127.0.0.1` only, because the crops of a commercial map never leave the owner's machine, and prints
the address to open. The page shows one call at a time: the zoomed crop and the wider crop, the member, map,
style, kind, and item, each reading, the settled value, the adversarial pass's value, and the settler's reason. It
starts at the first call with no verdict, so the owner can stop and come back. Pressing `r` records the call as
right, `w` as wrong, and `u` as unsure, and moves to the next call. The left and right arrow keys move between
calls without recording anything, and `n` opens a note that the next verdict on that call records with it. The page
shows how many calls have a verdict. Its words are in sentence case.

The server is a WSGI application from the standard library, with these routes:

- `GET /`: the page, as `text/html; charset=utf-8`. The page's script reads `/api/sample` and posts to
  `/api/verdict`.
- `GET /api/sample`: `{"calls": [...], "verdicts": {...}}` as `application/json`. `calls` are the sample's calls
  in order, each as the JSON of a [`SampleCall`][tools.answer_keys.sample.SampleCall], and `verdicts` maps each
  call id that has a verdict to the JSON of its [`Verdict`][tools.answer_keys.sample.Verdict].
- `GET /crops/<path>`: the bytes of a crop, where `<path>` is exactly a `zoom` or `wide` path that a call names,
  with the content type of its extension (`image/png` or `image/jpeg`). Any other path is a 404.
- `POST /api/verdict`: a JSON [`Verdict`][tools.answer_keys.sample.Verdict]. It replaces any earlier verdict on
  that call, writes the verdicts file, and answers `{"recorded": <number of calls with a verdict>}`. A body that
  isn't a valid verdict is a 400, and a call the sample doesn't have is a 404. Neither changes the file.

The verdicts file is [`verdicts_path`][tools.answer_keys.sample.verdicts_path] of the sample. Its verdicts are in
the sample's order of calls, and every write replaces the whole file through a temporary file in the same
directory, so a crash never leaves half a file. The application reads the file when it starts, so a new start
resumes where the last one stopped.
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

__all__ = ["main", "make_app"]

StartResponse = Callable[[str, list[tuple[str, str]]], Any]
WSGIApp = Callable[[dict[str, Any], StartResponse], Iterable[bytes]]


def make_app(sample: Path) -> WSGIApp:
    """Build the sample app's WSGI application for one sample file.

    Args:
        sample: The sample file, a [`SampleSet`][tools.answer_keys.sample.SampleSet] in JSON. Its crops are
            resolved against its directory.

    Returns:
        The WSGI application.

    Raises:
        OSError: If the sample file can't be read.
        pydantic.ValidationError: If the sample file or an existing verdicts file isn't valid.
    """
    raise NotImplementedError("chunk: sample-app")


def main(argv: list[str] | None = None) -> int:
    """Serve the sample app on `127.0.0.1` until interrupted.

    Args:
        argv: The command-line arguments after the program name: the sample file, and `--port` (default 8765).

    Returns:
        The exit status.
    """
    raise NotImplementedError("chunk: sample-app")


if __name__ == "__main__":
    raise SystemExit(main())
