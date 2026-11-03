"""Claude requests through the Batches API, charged to the shared ledger.

A *batch store* is a directory of jobs and their answers: `jobs/<custom id>.json` holds each request's exact
body and its meta (what it is for), `answers/<custom id>.json` holds the answer with its usage and cost, and
`batches.json` records each batch sent, its ledger entry, and the reservation made for each job. `submit` sends
every job that has no answer and no open batch, reserving each job's worst case on the ledger first; `collect`
fetches the answers of every batch that has ended and settles each reservation at the answer's cost, at the
Batches API's half price. A job whose result is an error is settled at nothing, because the API charges
nothing for it, and the job is left without an answer so the next `submit` sends it again.

The API key is the `ANTHROPIC_API_KEY=` line of `~/repos/re-pod/.env`, read inside this process and never
printed or written.
"""

import base64
import io
import json
import math
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from osrforge.providers.claude import PRICES
from osrforge.providers.ledger import Ledger, Reservation

__all__ = ["LEDGER_PATH", "SCRIPT", "BatchStore", "api_key", "batch_cost", "worst_case"]

LEDGER_PATH = Path.home() / ".re-pod/map-plan/claude-ledger.json"
SCRIPT = "answer-keys"
BATCH_DISCOUNT = 0.5
"""The Batches API charges half the list price for every kind of token."""

BATCH_BYTES = 180_000_000
"""The most bytes of job files one batch carries, under the API's 256 MB limit on a request body."""

_MAX_IMAGE_EDGE = 2576
_MAX_IMAGE_PIXELS = 3_750_000


def api_key() -> str:
    """Return the API key from `~/repos/re-pod/.env`."""
    env = Path.home() / "repos/re-pod/.env"
    for line in env.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit("no ANTHROPIC_API_KEY in ~/repos/re-pod/.env")


def _image_tokens(data: str) -> int:
    with Image.open(io.BytesIO(base64.b64decode(data))) as image:
        width, height = image.size
    scale = min(1.0, _MAX_IMAGE_EDGE / max(width, height), math.sqrt(_MAX_IMAGE_PIXELS / (width * height)))
    width, height = math.ceil(width * scale), math.ceil(height * scale)
    return -(-(width * height) // 750)


def estimate_input_tokens(params: dict[str, Any]) -> int:
    """Estimate a request's input tokens from above: one token per text byte, an image at a token per 750 pixels.

    Args:
        params: The request's parameters.

    Returns:
        The estimate, plus 64.
    """
    total = 64
    for block in params.get("system", []):
        total += len(block["text"].encode("utf-8"))
    schema = params.get("output_config", {}).get("format", {}).get("schema")
    if schema is not None:
        total += len(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    for message in params["messages"]:
        content = message["content"]
        if isinstance(content, str):
            total += len(content.encode("utf-8"))
            continue
        for block in content:
            if block["type"] == "text":
                total += len(block["text"].encode("utf-8"))
            elif block["type"] == "image":
                total += _image_tokens(block["source"]["data"])
    return total


def worst_case(params: dict[str, Any]) -> float:
    """A request's worst-case cost in a batch: its input at the cache-write price and `max_tokens` at the output
    price, at half price.

    Args:
        params: The request's parameters.

    Returns:
        Dollars, rounded to micro-dollars.
    """
    prices = PRICES[params["model"]]
    full = estimate_input_tokens(params) * prices.cache_write + params["max_tokens"] * prices.output
    return round(full * BATCH_DISCOUNT, 6)


def batch_cost(model: str, usage: dict[str, Any]) -> float:
    """What a batch answer cost at half the list price.

    Args:
        model: The model id.
        usage: The answer's `usage` object as the API returns it.

    Returns:
        Dollars, rounded to micro-dollars.
    """
    prices = PRICES[model]
    creation = usage.get("cache_creation") or {}
    write = usage.get("cache_creation_input_tokens") or 0
    write_1h = creation.get("ephemeral_1h_input_tokens") or 0
    full = (
        (usage.get("input_tokens") or 0) * prices.input
        + (usage.get("output_tokens") or 0) * prices.output
        + (write - write_1h) * prices.cache_write
        + write_1h * prices.cache_write * 2
        + (usage.get("cache_read_input_tokens") or 0) * prices.cache_read
    )
    return round(full * BATCH_DISCOUNT, 6)


class BatchStore:
    """One directory of jobs, batches, and answers."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.jobs = directory / "jobs"
        self.answers = directory / "answers"
        self.index = directory / "batches.json"
        self.added: list[str] = []
        """The jobs this store object wrote, so a collect resends only the retries it queued."""

    def _batches(self) -> dict[str, Any]:
        if self.index.exists():
            return json.loads(self.index.read_text())
        return {"batches": []}

    def _save_batches(self, data: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index.write_text(json.dumps(data, indent=1) + "\n")

    def add_job(self, custom_id: str, params: dict[str, Any], meta: dict[str, Any]) -> Path:
        """Write a job. A job that already exists is left as it is.

        Args:
            custom_id: The job's id, unique in the store.
            params: The request's parameters.
            meta: What the job is for, kept beside the answer.

        Returns:
            The job file.
        """
        self.jobs.mkdir(parents=True, exist_ok=True)
        path = self.jobs / f"{custom_id}.json"
        if not path.exists():
            path.write_text(json.dumps({"custom_id": custom_id, "meta": meta, "params": params}))
            self.added.append(custom_id)
        return path

    def job(self, custom_id: str) -> dict[str, Any]:
        """Read a job back."""
        return json.loads((self.jobs / f"{custom_id}.json").read_text())

    def job_ids(self) -> list[str]:
        """Every job's id, sorted."""
        return sorted(path.stem for path in self.jobs.glob("*.json")) if self.jobs.exists() else []

    def answer(self, custom_id: str) -> dict[str, Any] | None:
        """A job's answer, or `None` when it has none yet."""
        path = self.answers / f"{custom_id}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def answered(self) -> set[str]:
        """The ids of the jobs that have answers."""
        return {path.stem for path in self.answers.glob("*.json")} if self.answers.exists() else set()

    def in_flight(self) -> set[str]:
        """The ids of the jobs in a batch that hasn't ended."""
        ids: set[str] = set()
        for batch in self._batches()["batches"]:
            if not batch.get("ended"):
                ids.update(batch["reservations"])
        return ids

    def pending(self) -> list[str]:
        """The jobs with no answer and no open batch."""
        done = self.answered() | self.in_flight()
        return [custom_id for custom_id in self.job_ids() if custom_id not in done]

    def submit(
        self, client: Any, ledger: Ledger, label: str, limit: int | None = None, ids: Sequence[str] | None = None
    ) -> str | None:
        """Send the pending jobs, reserving each job's worst case first, in batches under the API's size limit.

        The Batches API takes a request body of at most 256 MB, so the jobs are sent in batches whose job files
        total at most `BATCH_BYTES`. If a batch can't be created, its reservations are settled at nothing, because
        nothing was sent, and the error is raised.

        Args:
            client: An `anthropic.Anthropic` client.
            ledger: The Claude ledger.
            label: The ledger entry's label.
            limit: Send at most this many jobs, or every pending job.
            ids: Send only pending jobs among these, such as the retries a collect queued; `None` sends any.

        Returns:
            The last batch id, or `None` when nothing was pending.
        """
        pending = self.pending()
        if ids is not None:
            wanted = set(ids)
            pending = [custom_id for custom_id in pending if custom_id in wanted]
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            return None
        groups: list[list[str]] = [[]]
        size = 0
        for custom_id in pending:
            bytes_ = (self.jobs / f"{custom_id}.json").stat().st_size
            if groups[-1] and size + bytes_ > BATCH_BYTES:
                groups.append([])
                size = 0
            groups[-1].append(custom_id)
            size += bytes_
        last = None
        for number, group in enumerate(groups, 1):
            suffix = f" part {number}" if len(groups) > 1 else ""
            last = self._submit_group(client, ledger, f"{label}{suffix}", group)
        return last

    def _submit_group(self, client: Any, ledger: Ledger, label: str, group: list[str]) -> str:
        jobs = [self.job(custom_id) for custom_id in group]
        model = jobs[0]["params"]["model"]
        entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "script": SCRIPT, "label": label, "model": model}
        index = ledger.open(entry)
        reservations: dict[str, dict[str, Any]] = {}
        for job in jobs:
            usd = worst_case(job["params"])
            reservation = ledger.reserve(index, usd)
            reservations[job["custom_id"]] = {"index": reservation.index, "usd": reservation.usd}
        requests = [{"custom_id": job["custom_id"], "params": job["params"]} for job in jobs]
        try:
            batch = client.messages.batches.create(requests=requests)
        except Exception:
            for reservation in reservations.values():
                ledger.settle(Reservation(index=reservation["index"], usd=reservation["usd"]), 0.0)
            raise
        data = self._batches()
        data["batches"].append(
            {
                "id": batch.id,
                "label": label,
                "created": entry["t"],
                "model": model,
                "entry": index,
                "reservations": reservations,
                "ended": False,
            }
        )
        self._save_batches(data)
        return batch.id

    def collect(self, client: Any, ledger: Ledger) -> dict[str, int]:
        """Fetch the answers of every batch that has ended, and settle the ledger.

        Args:
            client: An `anthropic.Anthropic` client.
            ledger: The Claude ledger.

        Returns:
            Counts: `open` batches still running, `answered` jobs collected now, `errored` jobs whose result was
            an error.
        """
        data = self._batches()
        counts = {"open": 0, "answered": 0, "errored": 0}
        self.answers.mkdir(parents=True, exist_ok=True)
        for batch in data["batches"]:
            if batch.get("ended"):
                continue
            status = client.messages.batches.retrieve(batch["id"])
            if status.processing_status != "ended":
                counts["open"] += 1
                continue
            for result in client.messages.batches.results(batch["id"]):
                custom_id = result.custom_id
                reservation = batch["reservations"].get(custom_id)
                if reservation is None:
                    continue
                kind = result.result.type
                if kind == "succeeded":
                    message = result.result.message.model_dump(mode="json")
                    usage = message.get("usage", {})
                    cost = batch_cost(batch["model"], usage)
                    ledger.settle(Reservation(index=reservation["index"], usd=reservation["usd"]), cost)
                    (self.answers / f"{custom_id}.json").write_text(
                        json.dumps(
                            {
                                "custom_id": custom_id,
                                "batch": batch["id"],
                                "message": message,
                                "cost_usd": cost,
                                "stop_reason": message.get("stop_reason"),
                            },
                            indent=1,
                        )
                    )
                    counts["answered"] += 1
                else:
                    ledger.settle(Reservation(index=reservation["index"], usd=reservation["usd"]), 0.0)
                    error = result.result.model_dump(mode="json")
                    (self.directory / "errors.jsonl").open("a").write(
                        json.dumps({"custom_id": custom_id, "batch": batch["id"], "result": error}) + "\n"
                    )
                    counts["errored"] += 1
            batch["ended"] = True
            batch["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save_batches(data)
        return counts

    def spent(self) -> float:
        """The cost of every answer in the store, in dollars."""
        total = 0.0
        if self.answers.exists():
            for path in self.answers.glob("*.json"):
                total += json.loads(path.read_text()).get("cost_usd", 0.0)
        return round(total, 6)
