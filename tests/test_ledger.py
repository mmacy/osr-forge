"""The cost ledger: the shared files that record what Claude and Clef have cost, with a stop.

Every test runs on a temporary copy of a ledger file in the real ledgers' schemas. No test touches the real
ledgers under `~/.re-pod/map-plan/`.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from osrforge.errors import LedgerStopError, ProviderError
from osrforge.providers.ledger import Ledger, Reservation

CHUNK = "chunk: claude-provider"

CLAUDE_LEDGER = {
    "stop_usd": 160.0,
    "limit_usd": 200.0,
    "spent_usd": 0.016624,
    "entries": [
        {
            "t": "2026-10-10T21:13:44",
            "script": "cachetest",
            "label": "sonnet window A, effort low (writes system cache)",
            "model": "claude-sonnet-5-5",
            "cost_usd": 0.010011,
        },
        {
            "t": "2026-10-10T21:13:48",
            "script": "cachetest",
            "label": "sonnet window B, effort low (should read system cache)",
            "model": "claude-sonnet-5-5",
            "cost_usd": 0.006613,
        },
    ],
}
"""The Claude ledger's schema, with two of its real entries."""

CLEF_LEDGER = {
    "stop_usd": 8.0,
    "limit_usd": 10.0,
    "spent_usd": 0.168562,
    "entries": [
        {
            "date": "2026-10-08",
            "what": "Clef probe on Workers AI (conservative ledger; list price $0.12881)",
            "requests": 2474,
            "usd": 0.168562,
        }
    ],
}
"""The Clef ledger's schema, with its one real entry."""

CLAUDE_ENTRY = {"t": "2026-10-15T09:00:00", "script": "osrforge", "label": "probe low", "model": "claude-sonnet-5-5"}
CLEF_ENTRY = {"date": "2026-10-15", "what": "osrforge test run"}


def write_ledger(path: Path, content: dict[str, object]) -> Path:
    path.write_text(json.dumps(content, indent=1), encoding="utf-8")
    return path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def claude_path(tmp_path: Path) -> Path:
    return write_ledger(tmp_path / "claude-ledger.json", CLAUDE_LEDGER)


@pytest.fixture
def clef_path(tmp_path: Path) -> Path:
    return write_ledger(tmp_path / "clef-ledger.json", CLEF_LEDGER)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_reserve_and_settle_keep_every_existing_entry(claude_path: Path):
    ledger = Ledger(claude_path, cost_key="cost_usd")
    index = ledger.open(CLAUDE_ENTRY)
    reservation = ledger.reserve(index, 0.08)
    ledger.settle(reservation, 0.0312)

    after = load(claude_path)
    assert after["entries"][:2] == CLAUDE_LEDGER["entries"]
    assert after["stop_usd"] == 160.0
    assert after["limit_usd"] == 200.0
    assert after["entries"][2] == {**CLAUDE_ENTRY, "cost_usd": pytest.approx(0.0312)}
    assert after["spent_usd"] == pytest.approx(0.016624 + 0.0312)
    assert index == 2


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_reservation_counts_at_its_worst_case_until_it_is_settled(claude_path: Path):
    ledger = Ledger(claude_path, cost_key="cost_usd")
    index = ledger.open(CLAUDE_ENTRY)
    reservation = ledger.reserve(index, 0.08)
    assert reservation == Reservation(index=index, usd=0.08)

    during = load(claude_path)
    assert during["spent_usd"] == pytest.approx(0.016624 + 0.08)
    assert during["entries"][index]["cost_usd"] == pytest.approx(0.08)
    assert during["entries"][index]["pending"] == 1
    assert ledger.spent() == pytest.approx(0.016624 + 0.08)

    ledger.settle(reservation, 0.02)
    after = load(claude_path)
    assert "pending" not in after["entries"][index]
    assert after["entries"][index]["cost_usd"] == pytest.approx(0.02)
    assert after["spent_usd"] == pytest.approx(0.016624 + 0.02)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_refuses_a_reservation_that_would_pass_the_stop(claude_path: Path):
    ledger = Ledger(claude_path, cost_key="cost_usd", stop_usd=0.1)
    index = ledger.open(CLAUDE_ENTRY)
    before = claude_path.read_bytes()
    with pytest.raises(LedgerStopError) as excinfo:
        ledger.reserve(index, 0.09)
    message = str(excinfo.value)
    assert "0.09" in message and "0.1" in message
    assert claude_path.read_bytes() == before

    # A reservation that reaches the stop exactly is allowed.
    reservation = ledger.reserve(index, 0.1 - 0.016624)
    assert load(claude_path)["spent_usd"] == pytest.approx(0.1)
    ledger.settle(reservation, 0.0)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_open_reserved_writes_nothing_on_a_refusal_and_one_entry_otherwise(claude_path: Path):
    ledger = Ledger(claude_path, cost_key="cost_usd", stop_usd=0.1)
    before = claude_path.read_bytes()
    with pytest.raises(LedgerStopError):
        ledger.open_reserved(CLAUDE_ENTRY, 0.09)
    assert claude_path.read_bytes() == before

    reservation = ledger.open_reserved(CLAUDE_ENTRY, 0.05)
    during = load(claude_path)
    assert reservation == Reservation(index=2, usd=0.05)
    assert during["entries"][2] == {**CLAUDE_ENTRY, "cost_usd": pytest.approx(0.05), "pending": 1}
    ledger.settle(reservation, 0.01)
    assert load(claude_path)["entries"][2] == {**CLAUDE_ENTRY, "cost_usd": pytest.approx(0.01)}


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_amounts_are_whole_micro_dollars_written_to_six_places(tmp_path: Path):
    """0.1 + 0.2 is 0.30000000000000004 in floats; in micro-dollars it reaches a stop of 0.3 exactly."""
    path = write_ledger(tmp_path / "ledger.json", {**CLAUDE_LEDGER, "spent_usd": 0.1, "entries": []})
    ledger = Ledger(path, cost_key="cost_usd", stop_usd=0.3)
    reservation = ledger.open_reserved(CLAUDE_ENTRY, 0.2)
    text = path.read_text(encoding="utf-8")
    assert '"spent_usd": 0.3' in text and "0.30000000000000004" not in text
    ledger.settle(reservation, 0.1234567)
    after = load(path)
    assert after["entries"][0]["cost_usd"] == 0.123457
    assert after["spent_usd"] == 0.223457


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_stop_comes_from_the_file_unless_a_setting_overrides_it(claude_path: Path, clef_path: Path):
    assert Ledger(claude_path, cost_key="cost_usd").stop_usd == 160.0
    assert Ledger(clef_path, cost_key="usd", count_key="requests").stop_usd == 8.0
    assert Ledger(claude_path, cost_key="cost_usd", stop_usd=5.0).stop_usd == 5.0


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_per_run_entry_counts_the_requests_settled_against_it(clef_path: Path):
    ledger = Ledger(clef_path, cost_key="usd", count_key="requests")
    index = ledger.open(CLEF_ENTRY)
    assert load(clef_path)["entries"][index] == {**CLEF_ENTRY, "requests": 0, "usd": 0.0}
    for cost in (0.00007, 0.00008, 0.00009):
        ledger.settle(ledger.reserve(index, 0.0002), cost)

    after = load(clef_path)
    assert after["entries"][0] == CLEF_LEDGER["entries"][0]
    assert after["entries"][index] == {**CLEF_ENTRY, "requests": 3, "usd": pytest.approx(0.00024)}
    assert after["spent_usd"] == pytest.approx(0.168562 + 0.00024)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_concurrent_requests_lose_no_charge(claude_path: Path):
    threads, per_thread, cost = 8, 25, 0.001

    def run(thread: int) -> None:
        ledger = Ledger(claude_path, cost_key="cost_usd")
        for _ in range(per_thread):
            index = ledger.open({**CLAUDE_ENTRY, "label": f"thread {thread}"})
            ledger.settle(ledger.reserve(index, 0.002), cost)

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(run, range(threads)))

    after = load(claude_path)
    assert len(after["entries"]) == 2 + threads * per_thread
    assert after["spent_usd"] == pytest.approx(0.016624 + threads * per_thread * cost, abs=1e-6)
    assert not any("pending" in entry for entry in after["entries"])
    assert sum(entry["cost_usd"] for entry in after["entries"]) == pytest.approx(after["spent_usd"], abs=1e-6)


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_a_missing_or_malformed_ledger_is_a_provider_error(tmp_path: Path):
    with pytest.raises(ProviderError):
        Ledger(tmp_path / "absent.json", cost_key="cost_usd").spent()
    path = tmp_path / "no-stop.json"
    path.write_text(json.dumps({"spent_usd": 0.0, "entries": []}), encoding="utf-8")
    with pytest.raises(ProviderError):
        _ = Ledger(path, cost_key="cost_usd").stop_usd
    assert Ledger(path, cost_key="cost_usd", stop_usd=1.0).stop_usd == 1.0


@pytest.mark.xfail(reason=CHUNK, strict=True)
def test_the_file_keeps_its_shape_for_the_other_writers(claude_path: Path):
    """The shared ledger is read by re-pod's scripts too, so the four top-level keys stay and entries stay a list."""
    ledger = Ledger(claude_path, cost_key="cost_usd")
    index = ledger.open(CLAUDE_ENTRY)
    ledger.settle(ledger.reserve(index, 0.01), 0.005)
    after = load(claude_path)
    assert set(after) == {"stop_usd", "limit_usd", "spent_usd", "entries"}
    assert isinstance(after["entries"], list)
