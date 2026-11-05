"""The sample app: a local page where the owner checks an answer key's settled calls, one key press each.

Run it from the osr-forge checkout with a sample file the answer-key pipeline wrote:

```text
uv run python -m tools.answer_keys.sample_app <sample.json> [--port 8765]
```

It serves on `127.0.0.1` only, because the crops of a commercial map never leave the owner's machine, and prints
the address to open. The page shows one call at a time: the zoomed crop and the wider crop, the member, map, panel,
style, kind, category, and item, each reading, the settled value, the adversarial pass's value, and the settler's
reason. It starts at the first call with no verdict, so the owner can stop and come back. Pressing `r` records the
call as right, `w` as wrong, and `u` as unsure, and moves to the next call. The left and right arrow keys move
between calls without recording anything, and `n` opens a note that the next verdict on that call records with
it. Closing the note with Enter or Escape on a call that already has a verdict records that verdict again with the
note, so a note added afterwards is saved, and the page says so. The page shows how many calls have a verdict. Its
words are in sentence case.

The server is a WSGI application from the standard library. Every request whose `Host` header names a host other
than `127.0.0.1` or `localhost`, on any port, is a 403, so a page that points its own hostname at this machine
can't read the crops. The routes:

- `GET /`: the page, as `text/html; charset=utf-8`. The page's script reads `/api/sample` and posts to
  `/api/verdict`.
- `GET /api/sample`: `{"calls": [...], "verdicts": {...}}` as `application/json`. `calls` are the sample's calls
  in order, each as the JSON of a [`SampleCall`][tools.answer_keys.sample.SampleCall], and `verdicts` maps each
  call id that has a verdict to the JSON of its [`Verdict`][tools.answer_keys.sample.Verdict]. The JSON of a model
  holds every field, defaults included.
- `GET /crops/<path>`: the bytes of a crop, where `<path>` is exactly a `zoom` or `wide` path that a call names,
  with the content type [`CROP_TYPES`][tools.answer_keys.sample.CROP_TYPES] gives its extension. Any other path is
  a 404, and so is a named crop that is missing or that resolves, through a link, outside the sample file's
  directory.
- `POST /api/verdict`: a JSON [`Verdict`][tools.answer_keys.sample.Verdict], sent as `application/json`. Any other
  content type is a 415, which a page on another site can't send without the browser asking first, and the app
  never answers that question. A body that isn't a valid verdict is a 400, a call the sample doesn't have is a 404,
  and a verdict whose `settled` isn't the call's settled value is a 409, checked in that order. None of these
  changes the file. A valid verdict replaces any earlier verdict on that call, writes the verdicts file, and
  answers `{"recorded": <number of calls with a verdict>}`.
- Any other method on these paths is a 405, and any other path is a 404.

The verdicts file is [`verdicts_path`][tools.answer_keys.sample.verdicts_path] of the sample. Its verdicts are in
the sample's order of calls, and every write replaces the whole file through a temporary file in the same
directory, so a crash never leaves half a file. The application reads the file when it starts, so a new start
resumes where the last one stopped. A verdicts file in another order is read as it stands. One that names a call
the sample doesn't have, or whose verdict judged a settled value other than the call's, stops the start, because
the sample changed after those verdicts were given.

`main` serves the application with `make_server("127.0.0.1", port, app)`, the name imported into this module from
`wsgiref.simple_server`, and returns 0 when the server stops on an interrupt.
"""

import argparse
import contextlib
import json
import os
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, make_server

from pydantic import ValidationError
from tools.answer_keys.sample import CROP_TYPES, SampleSet, Verdict, Verdicts, verdicts_path

__all__ = ["main", "make_app"]

StartResponse = Callable[[str, list[tuple[str, str]]], Any]
WSGIApp = Callable[[dict[str, Any], StartResponse], Iterable[bytes]]

_STATUS = {
    200: "200 OK",
    400: "400 Bad Request",
    403: "403 Forbidden",
    404: "404 Not Found",
    405: "405 Method Not Allowed",
    409: "409 Conflict",
    415: "415 Unsupported Media Type",
}
_HOSTS = frozenset({"127.0.0.1", "localhost"})
_NO_STORE = ("Cache-Control", "no-store")


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
        ValueError: If the verdicts file names a call the sample doesn't have, or judged another settled value.
    """
    sample_set = SampleSet.model_validate_json(sample.read_text(encoding="utf-8"))
    calls = {call.id: call for call in sample_set.calls}
    crops = {path: sample.parent / path for call in sample_set.calls for path in (call.zoom, call.wide)}
    target = verdicts_path(sample)
    verdicts: dict[str, Verdict] = {}
    if target.exists():
        for verdict in Verdicts.model_validate_json(target.read_text(encoding="utf-8")).verdicts:
            if verdict.call not in calls:
                raise ValueError(f"{target} has a verdict on {verdict.call!r}, which the sample doesn't have")
            if verdict.settled != calls[verdict.call].settled:
                raise ValueError(
                    f"{target} judged {verdict.settled!r} on {verdict.call!r}, which the sample now settles"
                )
            verdicts[verdict.call] = verdict
    root = sample.parent.resolve()
    lock = threading.Lock()

    def sample_json() -> bytes:
        body = {
            "calls": [call.model_dump(mode="json") for call in sample_set.calls],
            "verdicts": {call_id: verdict.model_dump(mode="json") for call_id, verdict in verdicts.items()},
        }
        return json.dumps(body).encode("utf-8")

    def record(verdict: Verdict) -> int:
        """Replace the call's verdict, write the whole file, and only then keep the change in memory."""
        updated = {**verdicts, verdict.call: verdict}
        ordered = Verdicts(verdicts=tuple(updated[call_id] for call_id in calls if call_id in updated))
        _write_atomically(target, ordered.model_dump_json(indent=2) + "\n")
        verdicts.update({verdict.call: verdict})
        return len(verdicts)

    def app(environ: dict[str, Any], start_response: StartResponse) -> Iterable[bytes]:
        method = environ["REQUEST_METHOD"]
        path = environ.get("PATH_INFO", "")

        def reply(status: int, body: bytes, content_type: str, *extra: tuple[str, str]) -> Iterable[bytes]:
            headers = [("Content-Type", content_type), ("Content-Length", str(len(body))), *extra]
            start_response(_STATUS[status], headers)
            return [body]

        def fail(status: int, message: str) -> Iterable[bytes]:
            return reply(status, json.dumps({"error": message}).encode("utf-8"), "application/json", _NO_STORE)

        host = environ.get("HTTP_HOST", "").partition(":")[0].lower()
        if host not in _HOSTS:
            return fail(403, "the app answers only to 127.0.0.1 and localhost")
        allowed = {"/": "GET", "/api/sample": "GET", "/api/verdict": "POST"}
        crop = path[len("/crops/") :] if path.startswith("/crops/") else None
        route_method = "GET" if crop is not None and crop in crops else allowed.get(path)
        if route_method is not None and method != route_method:
            return reply(
                405,
                b'{"error": "method not allowed"}',
                "application/json",
                ("Allow", route_method),
                _NO_STORE,
            )
        if path == "/":
            return reply(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8", _NO_STORE)
        if path == "/api/sample":
            with lock:
                body = sample_json()
            return reply(200, body, "application/json", _NO_STORE)
        if path == "/api/verdict":
            if environ.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != "application/json":
                return fail(415, "a verdict is posted as application/json")
            try:
                length = int(environ.get("CONTENT_LENGTH") or 0)
                if length < 0:
                    raise ValueError("a body's length isn't negative")
                verdict = Verdict.model_validate_json(environ["wsgi.input"].read(length))
            except ValueError:
                return fail(400, "the body isn't a valid verdict")
            if verdict.call not in calls:
                return fail(404, f"the sample has no call {verdict.call!r}")
            if verdict.settled != calls[verdict.call].settled:
                return fail(409, f"the settled value of {verdict.call!r} isn't {verdict.settled!r}")
            with lock:
                recorded = record(verdict)
            return reply(200, json.dumps({"recorded": recorded}).encode("utf-8"), "application/json", _NO_STORE)
        if crop is not None and crop in crops:
            file = crops[crop]
            try:
                if not file.resolve().is_relative_to(root):
                    return fail(404, "the crop is outside the sample's directory")
                data = file.read_bytes()
            except OSError:
                return fail(404, "the crop can't be read")
            return reply(200, data, CROP_TYPES[file.suffix.lower()])
        return fail(404, "no such route")

    return app


def _write_atomically(target: Path, text: str) -> None:
    """Replace `target` with `text` through a temporary file in the same directory."""
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        """Print nothing for each request."""


def main(argv: list[str] | None = None) -> int:
    """Serve the sample app on `127.0.0.1` until interrupted.

    Args:
        argv: The command-line arguments after the program name: the sample file, and `--port` (default 8765).

    Returns:
        The exit status.
    """
    parser = argparse.ArgumentParser(prog="sample_app", description="Check an answer key's settled calls.")
    parser.add_argument("sample", type=Path, help="the sample file the answer-key pipeline wrote")
    parser.add_argument("--port", type=int, default=8765, help="the port to serve on (default 8765)")
    args = parser.parse_args(argv)
    try:
        app = make_app(args.sample)
        server = make_server("127.0.0.1", args.port, app, handler_class=_QuietHandler)
    except (OSError, ValidationError, ValueError) as error:
        print(f"sample_app: {error}", file=sys.stderr)
        return 1
    print(f"Open http://127.0.0.1:{args.port}/ to check {args.sample}. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Check a sample</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #f6f6f3; --panel: #ffffff; --ink: #1d1d1b; --muted: #61615b; --line: #d8d8d0;
  --right: #1f7a3d; --wrong: #b3261e; --unsure: #8a6100; --accent: #2457b5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161715; --panel: #20211e; --ink: #ecece6; --muted: #a2a299; --line: #3a3b36;
    --right: #5fc47f; --wrong: #ff8a80; --unsure: #e2b84a; --accent: #8fb0ff;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.45 system-ui, sans-serif; }
header { display: flex; flex-wrap: wrap; gap: 4px 24px; align-items: baseline; padding: 12px 16px;
  border-bottom: 1px solid var(--line); background: var(--panel); position: sticky; top: 0; }
h1 { margin: 0; font-size: 18px; }
header span { color: var(--muted); }
main { max-width: 1100px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
.crops { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
figure { margin: 0; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
figure img { display: block; margin: 0 auto; max-width: 100%; max-height: 46vh; background: #888; }
figcaption { margin-top: 8px; color: var(--muted); font-size: 14px; }
section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 16px; }
h2 { margin: 0 0 8px; font-size: 14px; color: var(--muted); font-weight: 600; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 0; }
dt { color: var(--muted); }
dd { margin: 0; overflow-wrap: anywhere; }
code, .value { font-family: ui-monospace, Menlo, monospace; }
.settled { font-weight: 700; }
.verdict-right { color: var(--right); }
.verdict-wrong { color: var(--wrong); }
.verdict-unsure { color: var(--unsure); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
button { font: inherit; color: var(--ink); background: var(--bg); border: 1px solid var(--line); border-radius: 6px;
  padding: 8px 14px; cursor: pointer; }
button:hover { border-color: var(--accent); }
button.right { border-color: var(--right); } button.wrong { border-color: var(--wrong); }
button.unsure { border-color: var(--unsure); }
kbd { font: 13px ui-monospace, Menlo, monospace; border: 1px solid var(--line); border-radius: 4px;
  padding: 0 5px; margin-left: 6px; color: var(--muted); }
textarea { display: block; width: 100%; margin-top: 8px; font: inherit; color: var(--ink); background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 8px; }
textarea[hidden] { display: none; }
#message { min-height: 1.45em; color: var(--muted); margin-top: 8px; }
#message.error { color: var(--wrong); }
.fatal { padding: 16px; color: var(--wrong); }
</style>
</head>
<body>
<header>
  <h1>Check a sample</h1>
  <span id="position"></span>
  <span id="progress"></span>
</header>
<main id="main" hidden>
  <div class="crops">
    <figure><img id="zoom" alt="Zoomed crop of the item"><figcaption>Zoomed crop</figcaption></figure>
    <figure><img id="wide" alt="Wider crop around the item"><figcaption>Wider crop</figcaption></figure>
  </div>
  <section>
    <h2>The call</h2>
    <dl id="facts"></dl>
  </section>
  <section>
    <h2>Readings</h2>
    <dl id="readings"></dl>
  </section>
  <section>
    <h2>Settled</h2>
    <dl id="settlement"></dl>
  </section>
  <section>
    <h2>Your verdict</h2>
    <div id="current"></div>
    <div class="actions">
      <button type="button" class="right" data-verdict="right">Right<kbd>r</kbd></button>
      <button type="button" class="wrong" data-verdict="wrong">Wrong<kbd>w</kbd></button>
      <button type="button" class="unsure" data-verdict="unsure">Unsure<kbd>u</kbd></button>
      <button type="button" id="note-button">Add a note<kbd>n</kbd></button>
      <button type="button" id="previous">Previous<kbd>&larr;</kbd></button>
      <button type="button" id="next">Next<kbd>&rarr;</kbd></button>
    </div>
    <textarea id="note" rows="3" hidden aria-label="Note"
      placeholder="Note for this call's verdict. Press Enter when done."></textarea>
    <div id="message" role="status"></div>
  </section>
</main>
<script>
"use strict";
const state = { calls: [], verdicts: new Map(), notes: new Map(), index: 0, busy: false };
const $ = (id) => document.getElementById(id);
const KINDS = { cell: "Cell", edge: "Edge", key_placement: "Key placement", passage_step: "Passage step" };
const CATEGORIES = {
  cave: "Cave",
  diagonal: "Diagonal",
  key: "Key",
  key_placement: "Key placement",
  passage: "Passage",
};
const READERS = { claude: "Claude", classifier: "Classifier", clef: "Clef" };

function rows(list, pairs) {
  list.replaceChildren();
  for (const [name, value, className] of pairs) {
    const dt = document.createElement("dt");
    dt.textContent = name;
    const dd = document.createElement("dd");
    dd.textContent = value;
    if (className) dd.className = className;
    list.append(dt, dd);
  }
}

function cropUrl(path) {
  return "/crops/" + path.split("/").map(encodeURIComponent).join("/");
}

function say(text, isError) {
  const message = $("message");
  message.textContent = text;
  message.className = isError ? "error" : "";
}

function render() {
  const call = state.calls[state.index];
  const verdict = state.verdicts.get(call.id);
  $("position").textContent = "Call " + (state.index + 1) + " of " + state.calls.length;
  $("progress").textContent = state.verdicts.size + " of " + state.calls.length + " calls have a verdict";
  $("zoom").src = cropUrl(call.zoom);
  $("wide").src = cropUrl(call.wide);
  rows($("facts"), [
    ["Member", call.member, "value"],
    ["Map", call.map, "value"],
    ["Panel", call.panel, "value"],
    ["Style", call.style, "value"],
    ["Kind", KINDS[call.kind] || call.kind],
    ["Category", CATEGORIES[call.category] || call.category],
    ["Item", call.item, "value"],
  ]);
  rows($("readings"), call.readings.map((r) => [READERS[r.reader] || r.reader, r.value, "value"]));
  rows($("settlement"), [
    ["Settled value", call.settled, "value settled"],
    call.adversarial === null
      ? ["Adversarial pass", "Not checked"]
      : ["Adversarial pass", call.adversarial, "value"],
    ["Reason", call.reason === "" ? "None given" : call.reason],
  ]);
  const current = $("current");
  current.className = verdict ? "verdict-" + verdict.verdict : "";
  current.textContent = verdict ? "Recorded as " + verdict.verdict + "." : "No verdict yet.";
  const note = state.notes.get(call.id) || "";
  $("note").value = note;
  $("note").hidden = note === "" && document.activeElement !== $("note");
  const next = state.calls[state.index + 1];
  if (next) {
    new Image().src = cropUrl(next.zoom);
    new Image().src = cropUrl(next.wide);
  }
}

function move(step) {
  const index = state.index + step;
  if (index < 0 || index >= state.calls.length) return;
  state.index = index;
  say("", false);
  render();
}

function openNote() {
  $("note").hidden = false;
  $("note").focus();
}

async function send(call, verdict, note) {
  const response = await fetch("/api/verdict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ call: call.id, settled: call.settled, verdict: verdict, note: note }),
  });
  if (!response.ok) throw new Error("The server answered " + response.status + ".");
  await response.json();
  state.verdicts.set(call.id, { call: call.id, settled: call.settled, verdict: verdict, note: note });
}

async function record(verdict) {
  if (state.busy) return;
  state.busy = true;
  const recordedAt = state.index;
  const call = state.calls[recordedAt];
  const note = state.notes.get(call.id) || "";
  try {
    await send(call, verdict, note);
    if (recordedAt + 1 < state.calls.length) {
      state.index = recordedAt + 1;
      say("Recorded " + verdict + " on " + call.item + ".", false);
    } else {
      say("Recorded " + verdict + " on " + call.item + ". This is the last call.", false);
    }
    render();
  } catch (error) {
    say("Not recorded. " + error.message, true);
  } finally {
    state.busy = false;
  }
}

// A call that already has a verdict keeps a changed note only if that verdict is recorded again with it.
async function saveNote() {
  const call = state.calls[state.index];
  const verdict = state.verdicts.get(call.id);
  const note = state.notes.get(call.id) || "";
  if (!verdict || verdict.note === note) return;
  if (state.busy) {
    say("Note not saved. A verdict is still being recorded; close the note again.", true);
    return;
  }
  state.busy = true;
  try {
    await send(call, verdict.verdict, note);
    say("Saved the note with " + verdict.verdict + " on " + call.item + ".", false);
  } catch (error) {
    say("Note not saved. " + error.message, true);
  } finally {
    state.busy = false;
  }
}

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || state.calls.length === 0) return;
  if (event.target === $("note")) {
    if (event.key === "Escape" || (event.key === "Enter" && !event.shiftKey)) {
      event.preventDefault();
      $("note").blur();
      render();
      saveNote();
    }
    return;
  }
  const key = event.key.toLowerCase();
  const verdicts = { r: "right", w: "wrong", u: "unsure" };
  if (key in verdicts) {
    event.preventDefault();
    if (!event.repeat) record(verdicts[key]);
  } else if (key === "arrowleft") {
    event.preventDefault();
    move(-1);
  } else if (key === "arrowright") {
    event.preventDefault();
    move(1);
  } else if (key === "n") {
    event.preventDefault();
    openNote();
  }
});

$("note").addEventListener("input", () => {
  state.notes.set(state.calls[state.index].id, $("note").value);
});
for (const button of document.querySelectorAll("button[data-verdict]")) {
  button.addEventListener("click", () => { button.blur(); record(button.dataset.verdict); });
}
$("note-button").addEventListener("click", openNote);
$("previous").addEventListener("click", () => { $("previous").blur(); move(-1); });
$("next").addEventListener("click", () => { $("next").blur(); move(1); });

async function start() {
  try {
    const response = await fetch("/api/sample");
    if (!response.ok) throw new Error("The server answered " + response.status + ".");
    const data = await response.json();
    state.calls = data.calls;
    for (const [id, verdict] of Object.entries(data.verdicts)) {
      state.verdicts.set(id, verdict);
      state.notes.set(id, verdict.note);
    }
    const first = state.calls.findIndex((call) => !state.verdicts.has(call.id));
    state.index = first === -1 ? 0 : first;
    $("main").hidden = false;
    if (first === -1) say("Every call has a verdict. Showing the first call.", false);
    render();
  } catch (error) {
    const fatal = document.createElement("div");
    fatal.className = "fatal";
    fatal.textContent = "The sample didn't load. " + error.message;
    document.body.append(fatal);
  }
}
start();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
