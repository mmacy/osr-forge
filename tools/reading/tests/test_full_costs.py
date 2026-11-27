"""What the full run pays for, offline: every paid attempt kept, the phases that make a partial run's requests a full
run's, the spends held over invocations, and the checks that fail a run.

Nothing here sends a request or reads a real ledger: providers are stand-ins, and every ledger is a temporary file.
"""

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tools.reading.claude_run import RequestCap, ask
from tools.reading.full_fakes import FakeClaude, FakeClef
from tools.reading.full_members import Member, real_members, synthetic_members
from tools.reading.full_report_check import check_report
from tools.reading.full_settings import load_settings
from tools.reading.full_store import ClaudeStore, ClefStore, Counted, Sources
from tools.reading.offsets import check_offset, offset_kind

from osrforge.errors import ProviderError, SchemaValidationError
from osrforge.providers.base import TextPart
from osrforge.providers.decisions import DecisionRequest
from osrforge.providers.reading import ReadingRequest, ReadingResponse, ReadingUsage

TOOL = Path(__file__).resolve().parent.parent
ROOT = TOOL.parent.parent
SYNTHETIC = ROOT / "tests" / "assets" / "synthetic-maps"

SETTINGS = {
    "claude": {
        "whole_page": {"model": "claude-opus-5-5", "effort": "high"},
        "items": {"model": "claude-opus-5-5", "effort": "medium", "items_per_request": 30},
        "offset_pick": {"model": "claude-sonnet-5-5", "effort": "low"},
        "cap": 480,
    },
    "clef": {"backend": "local", "model": "clef-flash", "cloud_fallback": {"model": "clef-flash", "spend_usd": 0.95}},
    "decision": {
        "passable_threshold": {"tsr_blue": 0.7, "black_white": 0.9, "cave": 0.9, "dark_rock": 0.8},
        "epsilon": 0.05,
    },
}


def _settings_path(tmp_path: Path, ledger: Path | None = None, spend: float | None = None) -> Path:
    document = json.loads(json.dumps(SETTINGS))
    if ledger is not None:
        document["claude"]["ledger"] = str(ledger)
    if spend is not None:
        document["clef"]["cloud_fallback"]["spend_usd"] = spend
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(document))
    return path


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buffer, "PNG")
    return buffer.getvalue()


def _question(state: str) -> DecisionRequest:
    return DecisionRequest(
        tag="edge",
        image=_png(),
        state=state,
        question="q",
        options=(("open", None), ("wall", None)),
        model="clef-flash",
    )


def _members(*names: str) -> list[Member]:
    return [member for member in synthetic_members(SYNTHETIC) if member.id.split("/")[-1] in names]


def _offset(text: str = "q") -> ReadingRequest:
    return ReadingRequest(kind=offset_kind("low", "claude-sonnet-5-5"), shared=(), question=(TextPart(text=text),))


def _claude_ledger(tmp_path: Path, entries: int = 0) -> Path:
    ledger = tmp_path / "claude-ledger.json"
    held = [{"label": "reading-items medium", "model": "m", "cost_usd": 0.0}] * entries
    ledger.write_text(json.dumps({"stop_usd": 100.0, "spent_usd": 0.0, "entries": held}))
    return ledger


class _Status(Exception):
    """An API error with a status, as the SDK's errors carry one."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


class _Claude:
    """A stand-in Claude provider that charges each attempt on the ledger, as the provider does, then does as told.

    Each step is an answer to return, or an exception to raise after the charge.
    """

    def __init__(self, ledger: Path, steps: list[Any], charges: list[float] | None = None) -> None:
        self.settings = SimpleNamespace(ledger_path=ledger, model="claude-sonnet-5-5")
        self.steps = steps
        self.charges = charges or [0.01] * len(steps)
        self.sent: list[ReadingRequest] = []

    def read(self, request: ReadingRequest) -> ReadingResponse:
        step, charge = self.steps[len(self.sent)], self.charges[len(self.sent)]
        self.sent.append(request)
        document = json.loads(self.settings.ledger_path.read_text())
        label = f"{request.kind.name} {request.kind.effort}"
        document["entries"].append({"label": label, "model": request.kind.model, "cost_usd": charge})
        self.settings.ledger_path.write_text(json.dumps(document))
        if isinstance(step, BaseException):
            raise step
        return ReadingResponse(data=step, usage=ReadingUsage(input_tokens=10, output_tokens=5), model_id="m")


def _failure(status: int) -> ProviderError:
    error = ProviderError(f"the request failed with {status}")
    error.__cause__ = _Status(status)
    return error


def _live(tmp_path: Path, provider: _Claude, cap: int = 480) -> Sources:
    ledger = provider.settings.ledger_path
    return Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        claude=lambda request, check, about: ask(provider, request, check, RequestCap(ledger, cap)).to_json(),  # type: ignore[arg-type]
        claude_how="sent",
    )


def _check(sources: Sources, ledger: Path, before: dict[str, Any]) -> dict[str, Any]:
    from tools.reading.full_run import ledger_check

    return ledger_check(sources, (ledger, before), (None, None))["claude"]


# 3. A paid attempt is never lost.


def test_a_transient_failure_keeps_the_paid_attempts_stops_the_run_and_a_restart_counts_them(tmp_path: Path) -> None:
    from tools.reading.full_run import ledger_snapshot

    ledger = _claude_ledger(tmp_path)
    before = ledger_snapshot(ledger, "reading-", "cost_usd")
    failing = _Claude(ledger, [SchemaValidationError("not JSON"), _failure(529)], [0.02, 0.5])
    sources = _live(tmp_path, failing)
    assert sources.read(_offset(), check_offset, "pair", {"pair": "pair"}) is None
    assert sources.stopped is not None and "failed" in sources.stopped
    store = ClaudeStore(tmp_path / "claude")
    key = _offset().key("claude-sonnet-5-5")
    assert store.get("reading-offset", key) is None
    assert [attempt["cost_usd"] for attempt in store.pending("reading-offset", key)] == [0.02, 0.5]
    assert _check(sources, ledger, before) == {
        "ledger": {"requests": 2, "usd": 0.52},
        "tally": {"requests": 2, "usd": 0.52},
        "agree": True,
    }
    # Nothing more is sent once the run has stopped.
    assert sources.read(_offset("other"), check_offset, "pair2", {"pair": "pair2"}) is None
    assert len(failing.sent) == 2
    # A restart sends the request again and counts the earlier attempts on it, apart from its own.
    before = ledger_snapshot(ledger, "reading-", "cost_usd")
    answering = _Claude(ledger, [{"choice": "A", "reason": ""}], [0.03])
    restarted = _live(tmp_path, answering)
    outcome = restarted.read(_offset(), check_offset, "pair", {"pair": "pair"})
    assert outcome is not None and outcome["answer"] == {"choice": "A", "reason": ""}
    assert len(outcome["earlier_attempts"]) == 2 and outcome["cost_usd"] == 0.55
    assert store.pending("reading-offset", key) == []
    totals = restarted.tally.totals()
    assert totals["reading-offset sent"] == {"requests": 1, "answers": 1, "usd": 0.03}
    assert totals["reading-offset full run"] == {"requests": 2, "answers": 0, "usd": 0.52}
    assert _check(restarted, ledger, before)["agree"]
    # A later invocation reads the stored outcome with every attempt on it.
    later = Sources(clef_store=ClefStore(tmp_path / "clef.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    later.read(_offset(), check_offset, "pair", {"pair": "pair"})
    assert later.tally.totals()["reading-offset full run"] == {"requests": 3, "answers": 1, "usd": 0.55}


def _refusal(status: int, message: str) -> ProviderError:
    error = ProviderError(f"the 'reading-offset' request failed: Error code: {status} - {message}")
    error.__cause__ = _Status(status)
    return error


def test_a_first_refusal_keeps_the_attempt_pending_stops_the_run_and_names_the_request(tmp_path: Path) -> None:
    from tools.reading.full_run import run_failures

    ledger = _claude_ledger(tmp_path, entries=128)
    refusing = _Claude(ledger, [_refusal(404, "model: no-such-model")] * 20, [0.0] * 20)
    sources = _live(tmp_path, refusing)
    for number in range(20):
        sources.read(_offset(f"q{number}"), check_offset, f"pair{number}", {"pair": f"pair{number}"})
    # One request is refused, and nothing more is sent.
    assert len(refusing.sent) == 1 and len(json.loads(ledger.read_text())["entries"]) == 129
    store = ClaudeStore(tmp_path / "claude")
    key = _offset("q0").key("claude-sonnet-5-5")
    assert store.get("reading-offset", key) is None
    (pending,) = store.pending("reading-offset", key)
    assert pending["ended"] == "refused" and pending["status"] == 404
    assert sources.stopped is not None
    assert "refused the reading-offset request for pair0" in sources.stopped
    assert "model: no-such-model" in sources.stopped
    report = {"stopped": sources.stopped, "members": {}, "costs": {"rows_add_up": True}}
    assert any("pair0" in failure and "no-such-model" in failure for failure in run_failures(report))
    # Once the account or the request is put right, a rerun sends every request.
    answering = _Claude(ledger, [{"choice": "A", "reason": ""}] * 20)
    restarted = _live(tmp_path, answering)
    for number in range(20):
        restarted.read(_offset(f"q{number}"), check_offset, f"pair{number}", {"pair": f"pair{number}"})
    assert len(answering.sent) == 20 and restarted.stopped is None


def test_a_request_refused_again_by_a_later_invocation_is_stored_without_an_answer_and_the_run_goes_on(
    tmp_path: Path,
) -> None:
    ledger = _claude_ledger(tmp_path)
    first = _Claude(ledger, [_refusal(400, "max_tokens: too large")], [0.0])
    _live(tmp_path, first).read(_offset(), check_offset, "pair", {"pair": "pair"})
    second = _Claude(ledger, [_refusal(400, "max_tokens: too large"), {"choice": "B", "reason": ""}], [0.0, 0.01])
    sources = _live(tmp_path, second)
    outcome = sources.read(_offset(), check_offset, "pair", {"pair": "pair"})
    assert outcome is not None and outcome["answer"] is None and outcome["ended"] == "refused"
    assert len(outcome["earlier_attempts"]) == 1
    assert sources.stopped is None and len(sources.refused) == 1 and "pair" in sources.refused[0]
    # The run goes on to the next request.
    assert sources.read(_offset("next"), check_offset, "next", {"pair": "next"}) is not None
    # The request cost two cap entries, and is never sent again.
    third = _Claude(ledger, [{"choice": "A", "reason": ""}])
    stored = _live(tmp_path, third).read(_offset(), check_offset, "pair", {"pair": "pair"})
    assert stored is not None and stored["answer"] is None and third.sent == []
    assert len(json.loads(ledger.read_text())["entries"]) == 3


@pytest.mark.parametrize(
    "message",
    [
        "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing.",
        "You have reached your specified workspace API usage limits.",
    ],
)
def test_a_400_that_names_the_account_is_a_failure_and_never_final(tmp_path: Path, message: str) -> None:
    ledger = _claude_ledger(tmp_path)
    key = _offset().key("claude-sonnet-5-5")
    for invocation in range(3):
        provider = _Claude(ledger, [_refusal(400, message)], [0.0])
        sources = _live(tmp_path, provider)
        assert sources.read(_offset(), check_offset, "pair", {"pair": "pair"}) is None
        assert len(provider.sent) == 1 and sources.stopped is not None and "failed" in sources.stopped
        assert ClaudeStore(tmp_path / "claude").get("reading-offset", key) is None
        assert len(ClaudeStore(tmp_path / "claude").pending("reading-offset", key)) == invocation + 1


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_an_account_or_transient_failure_isnt_stored_as_final(tmp_path: Path, status: int) -> None:
    ledger = _claude_ledger(tmp_path)
    sources = _live(tmp_path, _Claude(ledger, [_failure(status)], [0.0]))
    assert sources.read(_offset(), check_offset, "pair", {"pair": "pair"}) is None
    assert sources.stopped is not None
    key = _offset().key("claude-sonnet-5-5")
    assert ClaudeStore(tmp_path / "claude").get("reading-offset", key) is None
    assert len(ClaudeStore(tmp_path / "claude").pending("reading-offset", key)) == 1


def test_a_request_the_cap_refuses_before_its_first_attempt_is_counted_missing_and_keeps_nothing(
    tmp_path: Path,
) -> None:
    ledger = _claude_ledger(tmp_path, entries=480)
    provider = _Claude(ledger, [{"choice": "A", "reason": ""}])
    sources = _live(tmp_path, provider, cap=480)
    assert sources.read(_offset(), check_offset, "pair", {"pair": "pair"}) is None
    assert provider.sent == [] and sources.stopped is not None and "cap of 480" in sources.stopped
    (entry,) = sources.tally.entries.values()
    assert entry.origin == "missing" and entry.requests == 0
    assert not (tmp_path / "claude" / "reading-offset").exists()


def test_an_interrupt_during_a_request_keeps_its_paid_attempts_and_stops_the_run(tmp_path: Path) -> None:
    ledger = _claude_ledger(tmp_path)
    provider = _Claude(ledger, [SchemaValidationError("not JSON"), KeyboardInterrupt()], [0.02, 0.9])
    sources = _live(tmp_path, provider)
    assert sources.read(_offset(), check_offset, "pair", {"pair": "pair"}) is None
    assert sources.stopped is not None and "interrupted" in sources.stopped
    key = _offset().key("claude-sonnet-5-5")
    pending = ClaudeStore(tmp_path / "claude").pending("reading-offset", key)
    assert [attempt["cost_usd"] for attempt in pending] == [0.02, 0.9]
    assert "KeyboardInterrupt" in pending[1]["error"]
    assert sources.tally.totals()["reading-offset sent"] == {"requests": 2, "answers": 0, "usd": 0.92}
    # Nothing more is sent.
    assert sources.read(_offset("other"), check_offset, "pair2", {"pair": "pair2"}) is None
    assert len(provider.sent) == 2


def test_a_second_interrupt_or_another_exception_keeps_the_paid_attempt_and_travels_on(tmp_path: Path) -> None:
    ledger = _claude_ledger(tmp_path)
    sources = _live(tmp_path, _Claude(ledger, [RuntimeError("a bug")], [0.4]))
    with pytest.raises(RuntimeError, match="a bug"):
        sources.read(_offset(), check_offset, "pair", {"pair": "pair"})
    key = _offset().key("claude-sonnet-5-5")
    assert [a["cost_usd"] for a in ClaudeStore(tmp_path / "claude").pending("reading-offset", key)] == [0.4]

    class CtrlCTwice(_Claude):
        """The first Ctrl-C stops the run while the request is in flight, and the second interrupts it."""

        def read(self, request: ReadingRequest) -> ReadingResponse:
            stopped.stop("interrupted (Ctrl-C)")
            return super().read(request)

    stopped = _live(tmp_path / "second", CtrlCTwice(ledger, [KeyboardInterrupt()], [0.5]))
    with pytest.raises(KeyboardInterrupt):
        stopped.read(_offset(), check_offset, "pair", {"pair": "pair"})
    assert [a["cost_usd"] for a in ClaudeStore(tmp_path / "second" / "claude").pending("reading-offset", key)] == [0.5]


def test_the_first_ctrl_c_stops_the_sources_and_the_second_ends_the_process(tmp_path: Path) -> None:
    import signal

    from tools.reading.full_run import _stop_on_ctrl_c

    sources = Sources(clef_store=ClefStore(tmp_path / "clef.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    previous = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, _stop_on_ctrl_c(sources))
        signal.raise_signal(signal.SIGINT)
        assert sources.stopped is not None and "Ctrl-C" in sources.stopped
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGINT)
    finally:
        signal.signal(signal.SIGINT, previous)


def test_a_run_interrupted_in_a_whole_page_read_finishes_writes_its_report_and_fails(tmp_path: Path) -> None:
    from tools.reading.full_run import run, run_failures

    settings = load_settings(_settings_path(tmp_path))
    members = _members("cellars")
    ledger = _claude_ledger(tmp_path)
    sources = _live(tmp_path, _Claude(ledger, [KeyboardInterrupt()], [1.2]))
    sources.clef = FakeClef({m.uid: m.screen for member in members for m in member.maps})
    report = run(members, [], settings, sources, stand_in=False).report
    assert report["stopped"] is not None and "interrupted" in report["stopped"]
    assert report["costs"]["rows_add_up"]
    assert any("stopped sending" in failure for failure in run_failures(report))


def test_the_cap_before_the_retry_keeps_the_paid_first_attempt(tmp_path: Path) -> None:
    from tools.reading.full_run import ledger_snapshot

    ledger = _claude_ledger(tmp_path, entries=479)
    before = ledger_snapshot(ledger, "reading-", "cost_usd")
    provider = _Claude(ledger, [SchemaValidationError("not JSON"), {"choice": "A", "reason": ""}], [0.02, 0.02])
    sources = _live(tmp_path, provider, cap=480)
    assert sources.read(_offset(), check_offset, "pair", {"pair": "pair"}) is None
    assert sources.stopped is not None and "cap of 480" in sources.stopped
    assert len(provider.sent) == 1
    key = _offset().key("claude-sonnet-5-5")
    assert len(ClaudeStore(tmp_path / "claude").pending("reading-offset", key)) == 1
    assert _check(sources, ledger, before)["agree"]


def test_a_run_whose_source_stops_still_finishes_and_fails(tmp_path: Path) -> None:
    from tools.reading.full_run import run, run_failures

    settings = load_settings(_settings_path(tmp_path))
    members = _members("fold-joined")
    ledger = _claude_ledger(tmp_path)
    sources = _live(tmp_path, _Claude(ledger, [_failure(529)] * 10))
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    sources.clef = FakeClef(maps)
    result = run(members, [], settings, sources, stand_in=False)
    report = result.report
    assert report["stopped"] is not None
    assert report["members"]["synthetic/fold-joined"]["resolution"].startswith("unread")
    assert any("stopped sending" in failure for failure in run_failures(report))
    assert report["costs"]["rows_add_up"]


# 1. The phases: a partial run sends only what the run of every member sends.


def _recording(members: list[Member], tmp: Path, owners: set[str] | None) -> tuple[Sources, list[tuple[str, str]]]:
    """Sources whose Claude makes the key's answers and counts as live, recording each request's kind and key."""
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    pages = {page.id: (page, member.document()) for member in members for page in member.pages}
    fake = FakeClaude(maps, pages)
    sent: list[tuple[str, str]] = []

    def claude(request: ReadingRequest, check: Any, about: dict[str, Any]) -> dict[str, Any]:
        assert request.kind.model is not None
        sent.append((request.kind.name, request.key(request.kind.model)))
        return fake(request, check, about)

    sources = Sources(
        clef_store=ClefStore(tmp / "clef.jsonl"),
        claude_store=ClaudeStore(tmp / "claude"),
        clef=FakeClef(maps),
        claude=claude,
        claude_how="sent",
        claude_owners=owners,
    )
    return sources, sent


def test_claude_for_reads_every_page_and_sends_only_item_requests_the_whole_run_sends(tmp_path: Path) -> None:
    from tools.reading.full_run import run

    settings = load_settings(_settings_path(tmp_path))
    members = _members("fold-apart", "fold-joined", "manor")
    whole, sent_whole = _recording(members, tmp_path / "whole", None)
    whole_report = run(members, [], settings, whole, stand_in=False).report
    chosen = next(member for member in members if member.id == "synthetic/manor")
    # The page ids are what a run before the phases held whole-page reads by. They hold nothing now.
    owners = {*(run_map.uid for run_map in chosen.maps), *(page.id for page in chosen.pages)}
    partial, sent_partial = _recording(members, tmp_path / "partial", owners)
    report = run(members, [], settings, partial, stand_in=False).report
    pages = {key for kind, key in sent_whole if kind == "reading-wholemap"}
    assert {key for kind, key in sent_partial if kind == "reading-wholemap"} == pages
    items_partial = {key for kind, key in sent_partial if kind == "reading-items"}
    items_whole = {key for kind, key in sent_whole if kind == "reading-items"}
    assert items_partial and items_partial <= items_whole
    assert report["calibration"] == whole_report["calibration"]


def test_a_live_run_sends_no_item_request_while_an_edge_lacks_a_clef_answer(tmp_path: Path) -> None:
    from tools.reading.full_run import run, run_failures

    settings = load_settings(_settings_path(tmp_path))
    members = _members("fold-joined")
    sources, sent = _recording(members, tmp_path, None)
    sources.clef = None
    report = run(members, [], settings, sources, stand_in=False).report
    kinds = {kind for kind, _key in sent}
    assert "reading-wholemap" in kinds and "reading-items" not in kinds
    assert report["items_held"] is not None and "edges lack a Clef answer" in report["items_held"]
    assert sum(report["waiting_for"]["edges"].values()) > 0
    assert any("no item request was sent" in failure for failure in run_failures(report))


def test_a_member_whose_page_is_unread_fails_the_run_and_the_report_check(tmp_path: Path) -> None:
    from tools.reading.full_costs import UNREAD_LEVEL
    from tools.reading.full_run import print_report, run, run_failures

    settings = load_settings(_settings_path(tmp_path))
    members = _members("fold-joined")
    maps = {run_map.uid: run_map.screen for member in members for run_map in member.maps}
    sources = Sources(
        clef_store=ClefStore(tmp_path / "clef.jsonl"),
        claude_store=ClaudeStore(tmp_path / "claude"),
        clef=FakeClef(maps),
    )
    report = run(members, [], settings, sources, stand_in=False).report
    member = report["members"]["synthetic/fold-joined"]
    assert member["resolution"].startswith("unread") and member["unread_pages"]
    assert report["levels"] == [] and report["costs"]["rows_add_up"]
    failures = run_failures(report | {"ledger_check": {}})
    assert any("without a whole-page answer" in failure and "synthetic/fold-joined" in failure for failure in failures)
    out = io.StringIO()
    assert check_report(report, out) >= 1
    assert "FAIL synthetic/fold-joined: it has scores" in out.getvalue()
    # The member's level costs are on rows named for what they are, not on rows named like a level's.
    named = report["costs"]["named_rows"]
    level_rows = [name for name in named if name.startswith("synthetic/fold-joined ")]
    assert level_rows == [] and any(name.endswith(UNREAD_LEVEL) for name in named)
    report["failures"] = failures
    printed = io.StringIO()
    print_report(report, printed)
    assert "FAILED: 1 members have a page without a whole-page answer" in printed.getvalue()


def test_the_bar_doesnt_judge_a_style_whose_member_the_stage_has_no_trace_of(tmp_path: Path) -> None:
    from tools.reading.full_costs import row_totals
    from tools.reading.full_pools import bar_table
    from tools.reading.full_run import print_report

    (member,) = real_members(
        [TOOL / "maps.json"], [TOOL / "pages.json"], [ROOT / "tools" / "answer_keys" / "keys.json"]
    )
    key = member.document()
    table = bar_table([member], {}, {member.id: key}, {member.id: key})
    assert table
    for row in table.values():
        assert row["passes"] is None and row["no_stage_trace"] == [member.id] and row["no_classifier_reading"] == []
    report: dict[str, Any] = {
        "members": {},
        "levels": [],
        "pools": {},
        "bar": table,
        "halves": [],
        "halves_apart": False,
        "costs": {"requests": {}, "rows_add_up": True, "named_rows": {}, "rows_added_up": row_totals({})},
    }
    out = io.StringIO()
    print_report(report, out)
    assert f"not judged, because the stage has no trace of {member.id}" in out.getvalue()
    checked = io.StringIO()
    check_report(report, checked)
    assert "FAIL bar" not in checked.getvalue()


def _main_args(tmp_path: Path, *extra: str) -> list[str]:
    return ["--settings", str(_settings_path(tmp_path)), "--out", str(tmp_path / "out"), *extra]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--claude", "live", "--only", "synthetic/manor"), "--only leaves members out"),
        (("--claude", "live", "--clef", "fake"), "--clef fake pays for item requests"),
        (("--claude", "live", "--stand-in"), "--stand-in reads with the key's"),
        (("--claude-for", "synthetic/no-such-member"), "no member is named synthetic/no-such-member"),
    ],
)
def test_main_refuses_a_live_run_that_would_pay_for_requests_a_full_run_wouldnt_reuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], message: str
) -> None:
    from tools.reading.full_run import main

    with pytest.raises(SystemExit):
        main(_main_args(tmp_path, "--synthetic", str(SYNTHETIC), *extra))
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_dry_run_records_its_inputs_and_a_live_run_needs_the_same_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import main

    monkeypatch.delenv("OSRFORGE_CLAUDE_LEDGER", raising=False)
    out = tmp_path / "out"
    # Without a dry run in --out, a live run refuses to start.
    with pytest.raises(SystemExit, match="needs a dry run"):
        main(_main_args(tmp_path, "--synthetic", str(SYNTHETIC), "--claude", "live"))
    # A dry run records its inputs: here, no member, so it is quick.
    assert main(_main_args(tmp_path, "--dry-run")) == 0
    inputs = json.loads((out / "dry-run.json").read_text())["inputs"]
    assert inputs["members"] == [] and inputs["screening"] is False and len(inputs["settings_sha256"]) == 64
    # A live run with members the dry run didn't have is refused, naming them.
    with pytest.raises(SystemExit) as refused:
        main(_main_args(tmp_path, "--synthetic", str(SYNTHETIC), "--claude", "live"))
    assert "this run lacks none and adds" in str(refused.value) and "synthetic/manor" in str(refused.value)
    assert "Run the dry run first with the same arguments" in str(refused.value)
    # A live run with the dry run's inputs gets past the check, to the ledger's.
    with pytest.raises(SystemExit, match="must name the settings' ledger"):
        main(_main_args(tmp_path, "--claude", "live"))
    # A changed settings file is refused too.
    document = json.loads((tmp_path / "settings.json").read_text())
    document["decision"]["epsilon"] = 0.04
    settings = tmp_path / "changed.json"
    settings.write_text(json.dumps(document))
    with pytest.raises(SystemExit, match="settings_sha256"):
        main(["--settings", str(settings), "--out", str(out), "--claude", "live"])
    capsys.readouterr()


# 5. The tally.


def test_a_request_one_twin_counts_missing_and_the_other_sends_is_counted_sent(tmp_path: Path) -> None:
    from tools.reading.full_reading import read_page
    from tools.reading.full_run import run

    settings = load_settings(_settings_path(tmp_path))
    members = _members("fold-apart", "fold-joined")
    joined = next(member for member in members if member.id == "synthetic/fold-joined")
    # Every page's read is stored, so both twins choose items and look up the requests they share.
    filling, _sent = _recording(members, tmp_path, None)
    for member in members:
        for page in member.pages:
            read_page(page, settings, filling, None)
    # Only the joined twin's item requests may go to the live stand-in, so the other twin counts its own missing.
    owners = {*(run_map.uid for run_map in joined.maps), *(page.id for page in joined.pages)}
    sources, sent = _recording(members, tmp_path, owners)
    assert sources.claude_how == "sent"
    report = run(members, [], settings, sources, stand_in=False).report
    assert report["items_held"] is None
    apart_keys = {
        key
        for (owner, kind), keys in sources.tally.keys.items()
        if owner.startswith("synthetic/fold-apart/")
        for key in keys
        if kind == "reading-items"
    }
    joined_sent = {key for kind, key in sent if kind == "reading-items"}
    shared = apart_keys & joined_sent
    assert shared
    for key in shared:
        assert sources.tally.entries[key].origin == "sent", sources.tally.entries[key]


# 2 and 9. Hosted Clef's spend over invocations, and the local server's failures.


def _envelope(tokens: int) -> bytes:
    return json.dumps(
        {
            "result": {
                "model": "clef-flash",
                "answers": {"edge": {"probabilities": {"open": 0.3, "wall": 0.7}}},
                "usage": {"input_tokens": tokens},
            }
        }
    ).encode()


def _hosted(monkeypatch: pytest.MonkeyPatch, ledger: Path, tokens: int) -> list[str]:
    """Hosted Clef as the real provider, with a stand-in transport, on a temporary ledger."""
    import osrforge.providers.clef as hosted_module

    real = hosted_module.ClefProvider
    posted: list[str] = []

    def transport(url: str, body: bytes, headers: Any) -> tuple[int, bytes]:
        posted.append(url)
        return 200, _envelope(tokens)

    monkeypatch.setattr(hosted_module, "ClefProvider", lambda settings: real(settings, transport=transport))
    monkeypatch.setenv("OSRFORGE_CLEF_ACCOUNT_ID", "stand-in-account")
    monkeypatch.setenv("OSRFORGE_CLEF_LEDGER", str(ledger))
    monkeypatch.setenv("OSRFORGE_CLEF_TOKEN", "stand-in-token")
    monkeypatch.delenv("OSRFORGE_CLEF_LEDGER_STOP", raising=False)
    return posted


def test_the_hosted_spend_holds_over_every_invocation_and_a_spent_out_question_stays_unanswered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import ClefSource, ledger_snapshot

    from osrforge.providers.clef import cost_usd, estimate_tokens

    ledger = tmp_path / "clef-ledger.json"
    other = {"date": "d", "what": "another run", "requests": 1, "usd": 0.1}
    ledger.write_text(json.dumps({"stop_usd": 8.0, "limit_usd": 10.0, "spent_usd": 0.1, "entries": [other]}))
    tokens = estimate_tokens(_question("s0"))
    each = cost_usd("clef-flash", tokens)
    # Each question reserves 1.5 times its cost and settles at its cost, so 3.2 of them fit two questions.
    posted = _hosted(monkeypatch, ledger, tokens)
    settings = load_settings(_settings_path(tmp_path, spend=round(3.2 * each, 6)))
    answered = 0
    for invocation in range(2):
        store = ClefStore(tmp_path / f"clef-{invocation}.jsonl")
        sources = Sources(clef_store=store, claude_store=ClaudeStore(tmp_path / "claude"))
        source = ClefSource("hosted", settings)
        sources.clef = source
        for number in range(4):
            question = _question(f"s{invocation}-{number}")
            answer = sources.decide(question, "m", f"{number},0:west", "clef-edge", {"edge": [[number, 0], "west"]})
            answered += answer is not None
        assert sources.stopped is None and source.hosted_spent_out is not None
        assert "holds back every item request" in source.hosted_spent_out
    assert answered == len(posted) == 2
    spent = ledger_snapshot(ledger, "reading full run", "usd")["usd"]
    assert spent <= settings.hosted_spend_usd and spent == pytest.approx(2 * each, abs=1e-6)


def test_hosted_clefs_settings_are_checked_before_anything_is_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import ClefSource

    for name in ("OSRFORGE_CLEF_ACCOUNT_ID", "OSRFORGE_CLEF_LEDGER", "OSRFORGE_CLEF_LOCAL_MODEL"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(_settings_path(tmp_path))
    with pytest.raises(SystemExit, match="hosted Clef can't run"):
        ClefSource("local", settings)
    ClefSource("local-only", settings)


def test_a_local_server_that_refuses_the_connection_stops_the_run_and_sends_nothing_to_hosted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import ClefSource

    import osrforge.providers.clef_local as local_module

    ledger = tmp_path / "clef-ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 8.0, "limit_usd": 10.0, "spent_usd": 0.0, "entries": []}))
    posted = _hosted(monkeypatch, ledger, 100)
    real = local_module.LocalClefProvider

    def refused(url: str, body: bytes, headers: Any) -> tuple[int, bytes]:
        raise ConnectionRefusedError("connection refused")

    def bad_answer(url: str, body: bytes, headers: Any) -> tuple[int, bytes]:
        return 200, b"not JSON"

    monkeypatch.delenv("OSRFORGE_CLEF_LOCAL_MODEL", raising=False)
    settings = load_settings(_settings_path(tmp_path))
    # A question local can't answer by itself goes to hosted.
    monkeypatch.setattr(
        local_module, "LocalClefProvider", lambda s: real(s, transport=bad_answer, sleep=lambda _: None)
    )
    sources = Sources(clef_store=ClefStore(tmp_path / "a.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    sources.clef = ClefSource("local", settings)
    assert sources.decide(_question("a"), "m", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) is not None
    assert len(posted) == 1
    # A server that refuses the connection stops the run, and nothing goes to hosted.
    monkeypatch.setattr(local_module, "LocalClefProvider", lambda s: real(s, transport=refused, sleep=lambda _: None))
    sources = Sources(clef_store=ClefStore(tmp_path / "b.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    sources.clef = ClefSource("local", settings)
    assert sources.decide(_question("b"), "m", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) is None
    assert sources.stopped is not None and "local Clef can't be reached" in sources.stopped
    assert sources.decide(_question("c"), "m", "1,0:west", "clef-edge", {"edge": [[1, 0], "west"]}) is None
    assert len(posted) == 1


@pytest.mark.parametrize("status", [404, 500])
def test_a_local_server_that_fails_three_questions_in_a_row_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    from tools.reading.full_run import ClefSource

    import osrforge.providers.clef_local as local_module

    ledger = tmp_path / "clef-ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 8.0, "limit_usd": 10.0, "spent_usd": 0.0, "entries": []}))
    posted = _hosted(monkeypatch, ledger, 100)
    real = local_module.LocalClefProvider
    answers = {"next": (status, b"model 'clef-flash' not found")}

    def transport(url: str, body: bytes, headers: Any) -> tuple[int, bytes]:
        return answers["next"]

    monkeypatch.delenv("OSRFORGE_CLEF_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(local_module, "LocalClefProvider", lambda s: real(s, transport=transport, sleep=lambda _: None))
    settings = load_settings(_settings_path(tmp_path))
    sources = Sources(clef_store=ClefStore(tmp_path / "a.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    sources.clef = ClefSource("local", settings)
    for number in range(6):
        sources.decide(_question(f"q{number}"), "m", f"{number},0:west", "clef-edge", {"edge": [[number, 0], "west"]})
    # Two questions went to hosted, the third failure stopped the run, and nothing more was asked.
    assert len(posted) == 2
    assert sources.stopped is not None and "failed 3 questions in a row" in sources.stopped
    assert str(status) in sources.stopped


def test_a_local_answer_resets_the_count_of_failures_in_a_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.reading.full_run import ClefSource

    monkeypatch.delenv("OSRFORGE_CLEF_LOCAL_MODEL", raising=False)
    source = ClefSource("local-only", load_settings(_settings_path(tmp_path)))
    answer = SimpleNamespace(probabilities={"open": 0.5, "wall": 0.5})
    replies: list[Any] = [
        ProviderError("404"),
        ProviderError("404"),
        answer,
        ProviderError("404"),
        ProviderError("404"),
    ]

    class Local:
        def decide(self, request: DecisionRequest) -> Any:
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

    source._local = Local()
    sources = Sources(clef_store=ClefStore(tmp_path / "a.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    sources.clef = source
    for number in range(5):
        sources.decide(_question(f"q{number}"), "m", f"{number},0:west", "clef-edge", {"edge": [[number, 0], "west"]})
    assert replies == [] and sources.stopped is None


def test_a_hosted_failure_counts_the_requests_and_dollars_the_ledger_shows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import HOSTED_LABEL, ClefSource, ledger_check, ledger_snapshot

    ledger = tmp_path / "clef-ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 8.0, "limit_usd": 10.0, "spent_usd": 0.0, "entries": []}))
    _hosted(monkeypatch, ledger, 100)
    settings = load_settings(_settings_path(tmp_path))
    source = ClefSource("hosted", settings)

    class Failing:
        """A hosted provider that fails before it reserves anything, so the ledger shows no request."""

        def decide(self, request: DecisionRequest) -> Any:
            raise ProviderError("the image can't be encoded")

    source._hosted = Failing()
    sources = Sources(clef_store=ClefStore(tmp_path / "a.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    sources.clef = source
    before = ledger_snapshot(ledger, HOSTED_LABEL, "usd", "requests")
    assert sources.decide(_question("a"), "m", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) is None
    assert sources.stopped is not None and "hosted Clef failed" in sources.stopped
    check = ledger_check(sources, (None, None), (ledger, before))["clef"]
    assert check["agree"], check


def test_an_interrupt_during_a_hosted_question_counts_its_charge_and_stops_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.reading.full_run import HOSTED_LABEL, ClefSource, ledger_check, ledger_snapshot

    import osrforge.providers.clef as hosted_module

    ledger = tmp_path / "clef-ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 8.0, "limit_usd": 10.0, "spent_usd": 0.0, "entries": []}))
    real = hosted_module.ClefProvider
    _hosted(monkeypatch, ledger, 100)

    def interrupted(url: str, body: bytes, headers: Any) -> tuple[int, bytes]:
        raise KeyboardInterrupt

    monkeypatch.setattr(hosted_module, "ClefProvider", lambda settings: real(settings, transport=interrupted))
    source = ClefSource("hosted", load_settings(_settings_path(tmp_path)))
    sources = Sources(clef_store=ClefStore(tmp_path / "a.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    sources.clef = source
    before = ledger_snapshot(ledger, HOSTED_LABEL, "usd", "requests")
    assert sources.decide(_question("a"), "m", "0,0:west", "clef-edge", {"edge": [[0, 0], "west"]}) is None
    assert sources.stopped is not None and "interrupted" in sources.stopped
    check = ledger_check(sources, (None, None), (ledger, before))["clef"]
    assert check["agree"] and check["ledger"]["requests"] == 1 and check["ledger"]["usd"] > 0, check


# 4. The checks fail the run.


def test_a_ledger_check_that_disagrees_or_rows_that_dont_add_up_fail_the_run_and_are_printed() -> None:
    from tools.reading.full_run import print_report, run_failures

    check = {"ledger": {"requests": 3, "usd": 0.3}, "tally": {"requests": 2, "usd": 0.2}, "agree": False}
    report: dict[str, Any] = {
        "members": {},
        "levels": [],
        "pools": {},
        "halves": [],
        "halves_apart": False,
        "costs": {"requests": {}, "rows_add_up": False},
        "ledger_check": {"claude": check},
        "stopped": None,
        "items_held": None,
    }
    failures = run_failures(report)
    assert len(failures) == 2
    report["failures"] = failures
    out = io.StringIO()
    print_report(report, out)
    text = out.getvalue()
    assert "claude ledger check: ledger" in text and "agree: False" in text
    assert text.count("FAILED: ") == 2
    report["costs"]["rows_add_up"] = True
    report["ledger_check"]["claude"] = check | {"agree": True}
    assert run_failures(report) == []


# 7. The estimate.


def test_the_estimate_counts_twins_shared_item_requests_once_and_bounds_only_by_unanswered_edges(
    tmp_path: Path,
) -> None:
    from tools.reading.full_run import RunResult, estimate

    settings = load_settings(_settings_path(tmp_path))
    items = [object()] * 40
    readings = {
        "twin/a": SimpleNamespace(style="cave", items=items),
        "twin/b": SimpleNamespace(style="cave", items=items),
        "stored/c": SimpleNamespace(style="cave", items=items),
        "short/d": SimpleNamespace(style="cave", items=[object()] * 10),
    }
    sources = Sources(clef_store=ClefStore(tmp_path / "clef.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    for owner, key in (("twin/a", "k1"), ("twin/b", "k1"), ("twin/a", "k2"), ("twin/b", "k2"), ("short/d", "k4")):
        sources.tally.add(Counted(key, owner, "reading-items", "missing", 0, 0.0, {}))
    for owner, key in (("stored/c", "k3"), ("stored/c", "k5")):
        sources.tally.add(Counted(key, owner, "reading-items", "full run", 1, 0.1, {}))
    result = RunResult(report={"unanswered": {"short/d edges between floor": 25}}, readings=readings)  # type: ignore[arg-type]
    figures = estimate(result, sources, settings, {"reading-items": 0.1})
    assert figures["totals"]["reading-items"] == 3
    # The twins' two requests once, the stored map's none, and the short map's 35 possible items in two requests.
    assert figures["totals"]["reading-items at most"] == 2 + 0 + 2
    assert figures["per"]["twin/b"]["shares its item requests with"] == "twin/a"


def test_the_estimates_bound_counts_twins_whose_item_lists_overlap_partly_once_each(tmp_path: Path) -> None:
    from tools.reading.full_run import RunResult, estimate

    settings = load_settings(_settings_path(tmp_path))
    items = [object()] * 40
    readings = {
        "twin/a": SimpleNamespace(style="cave", items=items),
        "twin/b": SimpleNamespace(style="cave", items=items),
    }
    sources = Sources(clef_store=ClefStore(tmp_path / "clef.jsonl"), claude_store=ClaudeStore(tmp_path / "claude"))
    for owner, key in (("twin/a", "shared"), ("twin/b", "shared"), ("twin/a", "a"), ("twin/b", "b")):
        sources.tally.add(Counted(key, owner, "reading-items", "missing", 0, 0.0, {}))
    unanswered = {"twin/a edges between floor": 5, "twin/b edges between floor": 5}
    result = RunResult(report={"unanswered": unanswered}, readings=readings)  # type: ignore[arg-type]
    figures = estimate(result, sources, settings, {"reading-items": 0.1})
    # The three known requests once each, but the bound gives each twin its own 45 items in two requests: the shared
    # request may be counted twice, which errs toward too many.
    assert figures["totals"]["reading-items"] == 3
    assert figures["totals"]["reading-items at most"] == 2 + 2


# 8. Writes a crash can't tear.


def test_a_torn_last_line_of_the_clef_store_is_reported_and_dropped(tmp_path: Path) -> None:
    path = tmp_path / "clef.jsonl"
    good = {"key": "a", "map": "m", "item": "i", "probabilities": {"open": 1.0}, "source": "local", "usd": 0.0}
    path.write_text(json.dumps(good) + "\n" + '{"key": "b", "map": "m", "prob')
    store = ClefStore(path)
    assert store.torn is not None and store.get("a") == {"open": 1.0} and store.get("b") is None
    store.put("c", "m", "i", {"wall": 1.0}, "local")
    reloaded = ClefStore(path)
    assert reloaded.torn is None and reloaded.get("c") == {"wall": 1.0} and len(reloaded) == 2
    path.write_text('{"torn\n' + json.dumps(good) + "\n")
    with pytest.raises(ValueError, match="isn't the last line"):
        ClefStore(path)


def test_a_clef_store_whose_last_line_lacks_its_newline_stays_loadable(tmp_path: Path) -> None:
    path = tmp_path / "clef.jsonl"
    good = {"key": "a", "map": "m", "item": "i", "probabilities": {"open": 1.0}, "source": "local", "usd": 0.0}
    path.write_text(json.dumps(good))
    store = ClefStore(path)
    assert store.torn is None and store.get("a") == {"open": 1.0}
    store.put("b", "m", "i", {"wall": 1.0}, "local")
    reloaded = ClefStore(path)
    assert reloaded.get("a") == {"open": 1.0} and reloaded.get("b") == {"wall": 1.0}


def test_a_claude_outcome_is_written_whole_or_not_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ClaudeStore(tmp_path / "claude")
    real = Path.write_text

    def torn(self: Path, text: str, *args: Any, **kwargs: Any) -> int:
        real(self, text[: len(text) // 2], *args, **kwargs)
        raise OSError("the disk is full")

    monkeypatch.setattr(Path, "write_text", torn)
    with pytest.raises(OSError, match="disk is full"):
        store.put("reading-offset", "k", {"answer": {"choice": "A"}, "attempts": [{}]})
    monkeypatch.setattr(Path, "write_text", real)
    assert store.get("reading-offset", "k") is None
    store.put("reading-offset", "k", {"answer": {"choice": "A"}, "attempts": [{}]})
    assert store.get("reading-offset", "k") == {"answer": {"choice": "A"}, "attempts": [{}], "key": "k"}


# 10. One run at a time.


def test_a_second_run_is_refused_while_the_first_holds_the_lock_whatever_the_file_says(tmp_path: Path) -> None:
    from tools.reading.full_run import LOCK, take_lock

    lock = take_lock(tmp_path)
    assert (tmp_path / LOCK).read_text().strip() == str(os.getpid())
    with pytest.raises(SystemExit, match="another full run"):
        take_lock(tmp_path)
    # A file that names a process that is gone, as a stale lock does, doesn't let a second run take the lock over.
    (tmp_path / LOCK).write_text("999999999\n")
    with pytest.raises(SystemExit, match="another full run"):
        take_lock(tmp_path)
    lock.close()
    # Once the holder lets go, or its process ends, the next run takes the lock.
    take_lock(tmp_path).close()


def test_main_releases_the_lock_when_it_ends(tmp_path: Path) -> None:
    from tools.reading.full_run import main, take_lock

    assert main(_main_args(tmp_path, "--claude", "fake", "--clef", "fake")) == 0
    take_lock(tmp_path / "out").close()


# 11. The offset pick's check.


def test_the_offset_check_refuses_a_choice_that_isnt_an_offered_letter_or_none() -> None:
    assert check_offset({"choice": "B", "reason": ""}) == []
    assert check_offset({"choice": "none", "reason": ""}) == []
    assert check_offset({"choice": "D", "reason": ""}) != []


# 12. The real provider's errors, as `ask` classifies them.


def _sdk_error(status: int, message: str) -> Exception:
    import anthropic
    import httpx2 as httpx

    request = httpx.Request("POST", "https://example.invalid/v1/messages")
    if status == 0:
        return anthropic.APIConnectionError(request=request)
    response = httpx.Response(
        status, request=request, json={"type": "error", "error": {"type": "error", "message": message}}
    )
    kinds = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        529: anthropic.InternalServerError,
    }
    return kinds[status](message, response=response, body={"type": "error", "error": {"message": message}})


@pytest.mark.parametrize(
    ("status", "message", "ended", "charged"),
    [
        (400, "max_tokens: 99999999 > 64000, which is the maximum allowed", "refused", False),
        (400, "Your credit balance is too low to access the Anthropic API.", "failed", False),
        (401, "invalid x-api-key", "failed", False),
        (404, "model: claude-no-such-model", "refused", False),
        (429, "rate limited", "failed", False),
        (529, "overloaded", "failed", True),
        (0, "", "failed", True),
    ],
)
def test_ask_classifies_the_real_providers_errors(
    tmp_path: Path, status: int, message: str, ended: str, charged: bool
) -> None:
    from pydantic import SecretStr
    from tools.reading.claude_run import ask

    from osrforge.providers.claude import ClaudeProvider, ClaudeSettings

    error = _sdk_error(status, message)

    class Client:
        """A client whose every stream raises the SDK's own error."""

        def __init__(self) -> None:
            self.messages = self
            self.calls = 0

        def stream(self, **body: Any) -> Any:
            self.calls += 1
            raise error

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"stop_usd": 100.0, "spent_usd": 0.0, "entries": []}))
    client = Client()
    settings = ClaudeSettings(api_key=SecretStr("stand-in"), ledger_path=ledger, model="claude-sonnet-5-5")
    provider = ClaudeProvider(settings, client=client, sleep=lambda seconds: None)
    outcome = ask(provider, _offset(), check_offset, RequestCap(ledger, 480))
    assert outcome.ended == ended and not outcome.final
    assert outcome.status == (status or None)
    assert message in (outcome.reason or "")
    entries = json.loads(ledger.read_text())["entries"]
    assert len(entries) == 1 and len(outcome.attempts) == 1
    assert (entries[0]["cost_usd"] > 0) == charged and outcome.attempts[0].cost_usd == entries[0]["cost_usd"]
    assert client.calls == (5 if status in (0, 429, 529) else 1)
