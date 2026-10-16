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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Ledger", "Reservation"]


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
        raise NotImplementedError("chunk: claude-provider")

    @property
    def stop_usd(self) -> float:
        """The stop: the setting when given, else the file's `stop_usd`."""
        raise NotImplementedError("chunk: claude-provider")

    def spent(self) -> float:
        """Return the file's `spent_usd`, which includes every open reservation at its worst case.

        Returns:
            The amount spent.
        """
        raise NotImplementedError("chunk: claude-provider")

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
        raise NotImplementedError("chunk: claude-provider")

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
        raise NotImplementedError("chunk: claude-provider")

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
        raise NotImplementedError("chunk: claude-provider")

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
        raise NotImplementedError("chunk: claude-provider")
