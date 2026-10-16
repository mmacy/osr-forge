"""The cost ledger: a file that records what a model service has cost, with a stop.

A ledger file is JSON: `{"stop_usd": ..., "limit_usd": ..., "spent_usd": ...,
"entries": [...]}`. Two ledgers exist, and their entries differ. The Claude
ledger's entries are `{"t", "script", "label", "model", "cost_usd"}`, one per
request. The Clef ledger's entries are `{"date", "what", "requests", "usd"}`,
one per run. The ledger code doesn't know either schema: the caller gives it
the entry and names the cost key (and, for a per-run entry, the request-count
key), and the ledger keeps every entry it finds in the file.

Spend is reserved before a request at its worst case and settled after it at
the cost the answer reports, so a request that fails after it was sent, or a
process that dies between the two, leaves its worst case on the ledger. A
request's retries share its one reservation.

The file is written under a lock that excludes threads and processes alike,
and any script that writes the same file follows the same protocol: take an
exclusive `fcntl.flock` on the sidecar file `<ledger path>.lock`, read the
ledger, change it, write it to a temporary file in the same directory,
`os.replace` the temporary file over the ledger, and release the lock.
Amounts are kept in whole micro-dollars and written rounded to six decimal
places, as the files already are, so the stop comparison is exact and
`spent_usd` never shows float noise.
"""

import fcntl
import json
import math
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from osrforge.errors import LedgerStopError, ProviderError

__all__ = ["Ledger", "Reservation"]

_MICROS_PER_USD = 1_000_000


def _micros(usd: float) -> int:
    return round(usd * _MICROS_PER_USD)


def _usd(micros: int) -> float:
    return round(micros / _MICROS_PER_USD, 6)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@dataclass(frozen=True)
class Reservation:
    """A reservation against one entry, to settle after the request.

    Attributes:
        index: The entry's index in the file's `entries`.
        usd: The amount reserved.
    """

    index: int
    usd: float


class Ledger:
    """One ledger file.

    Examples:
        ```python
        from pathlib import Path

        from osrforge.providers.ledger import Ledger

        ledger = Ledger(Path("claude-ledger.json"), cost_key="cost_usd")
        entry = {"t": "2026-10-15T10:00:00", "script": "osrforge", "label": "items p38", "model": "m"}
        reservation = ledger.open_reserved(entry, 0.08)
        # ... the request runs ...
        ledger.settle(reservation, 0.0312)
        ```
    """

    def __init__(
        self,
        path: Path,
        cost_key: str,
        stop_usd: float | None = None,
        count_key: str | None = None,
    ) -> None:
        """Bind to a ledger file without touching it.

        Args:
            path: The ledger file. It must exist, with the four top-level
                keys, when the first method that reads it runs.
            cost_key: The entry key that holds an entry's cost.
            stop_usd: The stop. When omitted, the file's `stop_usd` is the
                stop.
            count_key: The entry key that counts the requests settled against
                the entry, for a per-run entry. `None` for a per-request
                entry.
        """
        self.path = path
        self.cost_key = cost_key
        self.stop_usd_setting = stop_usd
        self.count_key = count_key

    def read(self) -> dict[str, object]:
        """Return the ledger file's content.

        Returns:
            The parsed file.

        Raises:
            ProviderError: If the file is missing, isn't JSON, or lacks
                `spent_usd` or `entries`, or if neither it nor the setting
                gives a stop.
        """
        return self._load()

    @property
    def stop_usd(self) -> float:
        """The stop: the setting when given, else the file's `stop_usd`."""
        if self.stop_usd_setting is not None:
            return self.stop_usd_setting
        return float(self._load()["stop_usd"])

    def spent(self) -> float:
        """Return the file's `spent_usd`, which includes every open reservation at its worst case.

        Returns:
            The amount spent.
        """
        return _usd(_micros(self._load()["spent_usd"]))

    def open(self, entry: Mapping[str, object]) -> int:
        """Append an entry with a cost of zero and return its index.

        The entry gets `cost_key` set to `0.0` and, when the ledger has a
        `count_key`, that key set to `0`. The caller supplies every other
        field in the file's own schema.

        Args:
            entry: The entry's fields.

        Returns:
            The entry's index in `entries`.
        """

        def change(ledger: dict[str, Any]) -> int:
            ledger["entries"].append(self._new_entry(entry, 0))
            return len(ledger["entries"]) - 1

        return self._update(change)

    def open_reserved(self, entry: Mapping[str, object], usd: float) -> Reservation:
        """Append an entry and reserve against it in one locked step, writing nothing on a refusal.

        The stop is checked before the entry is written, so a refused request
        leaves the file byte for byte as it was. Otherwise this is `open`
        followed by `reserve` under one lock.

        Args:
            entry: The entry's fields.
            usd: The worst-case cost.

        Returns:
            The reservation, to settle.

        Raises:
            LedgerStopError: If the reservation would take the spend past the
                stop.
        """
        amount = self._amount(usd)

        def change(ledger: dict[str, Any]) -> Reservation:
            spent = self._check_stop(ledger, amount)
            new = self._new_entry(entry, amount)
            new["pending"] = 1
            ledger["entries"].append(new)
            ledger["spent_usd"] = _usd(spent + amount)
            return Reservation(index=len(ledger["entries"]) - 1, usd=_usd(amount))

        return self._update(change)

    def reserve(self, index: int, usd: float) -> Reservation:
        """Reserve a request's worst-case cost against an entry.

        Under the lock: if the file's `spent_usd` plus `usd` would pass the
        stop, nothing is written and `LedgerStopError` is raised. Otherwise
        the entry's cost and the file's `spent_usd` grow by `usd`, and the
        entry's `pending` count grows by one. The comparison is made in whole
        micro-dollars, so a reservation that reaches the stop exactly is
        allowed.

        Args:
            index: The entry's index.
            usd: The worst-case cost.

        Returns:
            The reservation, to settle.

        Raises:
            LedgerStopError: If the reservation would take the spend past the
                stop. The message names the spend, the reservation, and the
                stop.
        """
        amount = self._amount(usd)

        def change(ledger: dict[str, Any]) -> Reservation:
            target = self._entry(ledger, index)
            spent = self._check_stop(ledger, amount)
            target[self.cost_key] = _usd(self._cost(target) + amount)
            target["pending"] = int(target.get("pending", 0)) + 1
            ledger["spent_usd"] = _usd(spent + amount)
            return Reservation(index=index, usd=_usd(amount))

        return self._update(change)

    def settle(self, reservation: Reservation, usd: float) -> None:
        """Replace a reservation with the cost the answer reports.

        Under the lock: the entry's cost and the file's `spent_usd` change by
        `usd` minus the reserved amount, the entry's `pending` count falls by
        one (and the key is removed at zero), and, when the ledger has a
        `count_key`, the entry's count grows by one. Settling may take the
        spend past the stop; the stop is checked only when reserving.

        Args:
            reservation: The reservation.
            usd: The actual cost.
        """
        delta = self._amount(usd) - _micros(reservation.usd)

        def change(ledger: dict[str, Any]) -> None:
            target = self._entry(ledger, reservation.index)
            target[self.cost_key] = _usd(self._cost(target) + delta)
            pending = int(target.get("pending", 1)) - 1
            if pending > 0:
                target["pending"] = pending
            else:
                target.pop("pending", None)
            if self.count_key is not None:
                target[self.count_key] = int(target.get(self.count_key, 0)) + 1
            ledger["spent_usd"] = _usd(_micros(ledger["spent_usd"]) + delta)

        self._update(change)

    def _load(self) -> dict[str, Any]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise ProviderError(f"can't read the ledger {self.path}: {error}") from error
        try:
            loaded: Any = json.loads(text)
        except json.JSONDecodeError as error:
            raise ProviderError(f"the ledger {self.path} isn't JSON: {error}") from error
        if not isinstance(loaded, dict):
            raise ProviderError(f"the ledger {self.path} isn't a JSON object")
        ledger = cast(dict[str, Any], loaded)
        if not _is_number(ledger.get("spent_usd")):
            raise ProviderError(f"the ledger {self.path} has no numeric spent_usd")
        if not isinstance(ledger.get("entries"), list):
            raise ProviderError(f"the ledger {self.path} has no entries list")
        if self.stop_usd_setting is None and not _is_number(ledger.get("stop_usd")):
            raise ProviderError(f"the ledger {self.path} has no numeric stop_usd, and no stop was set")
        return ledger

    def _update[T](self, change: Callable[[dict[str, Any]], T]) -> T:
        """Apply a change to the ledger under the lock, and write the file only if the change returns."""
        if not self.path.is_file():
            raise ProviderError(f"the ledger {self.path} doesn't exist")
        with open(self.path.with_name(f"{self.path.name}.lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                ledger = self._load()
                result = change(ledger)
                self._write(ledger)
                return result
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write(self, ledger: dict[str, Any]) -> None:
        # One-space indent and no trailing newline, as re-pod's scripts write the shared ledgers.
        text = json.dumps(ledger, indent=1)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(text)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(temporary, stat.S_IMODE(self.path.stat().st_mode))
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def _new_entry(self, entry: Mapping[str, object], amount: int) -> dict[str, object]:
        new = dict(entry)
        if self.count_key is not None:
            new[self.count_key] = 0
        new[self.cost_key] = _usd(amount)
        return new

    def _entry(self, ledger: dict[str, Any], index: int) -> dict[str, Any]:
        entries = ledger["entries"]
        if not 0 <= index < len(entries) or not isinstance(entries[index], dict):
            raise ProviderError(f"the ledger {self.path} has no entry {index}")
        return entries[index]

    def _cost(self, entry: dict[str, Any]) -> int:
        cost = entry.get(self.cost_key, 0)
        if not _is_number(cost):
            raise ProviderError(f"an entry of the ledger {self.path} has a {self.cost_key} that isn't a number")
        return _micros(cost)

    def _check_stop(self, ledger: dict[str, Any], amount: int) -> int:
        """Return the spend in micro-dollars, or raise if the amount would take it past the stop."""
        spent = _micros(ledger["spent_usd"])
        stop = _micros(self.stop_usd_setting if self.stop_usd_setting is not None else ledger["stop_usd"])
        if spent + amount > stop:
            raise LedgerStopError(
                f"the ledger {self.path} has spent ${_usd(spent):.6f}, and reserving ${_usd(amount):.6f} more "
                f"would pass its stop of ${_usd(stop):.6f}"
            )
        return spent

    @staticmethod
    def _amount(usd: float) -> int:
        if not _is_number(usd) or not math.isfinite(usd) or usd < 0:
            raise ValueError(f"an amount must be a number of dollars at least zero, not {usd!r}")
        return _micros(usd)
