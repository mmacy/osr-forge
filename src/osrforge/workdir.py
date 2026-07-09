"""The per-module working directory: layout paths, `run.json` I/O, and the artifact writer.

No other module builds workdir paths by hand, and every JSON artifact goes
through [`write_json_artifact`][osrforge.workdir.write_json_artifact] — pinning
the byte format once means the byte-stability tests that arrive with assembly
(phase 2) never chase formatting noise.
"""

import json
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from osrforge.contracts.run import RunMeta, Stage, StageStatus, TokenUsage

__all__ = ["StageTracker", "Workdir", "track_stage", "write_json_artifact"]


def write_json_artifact(path: Path, artifact: BaseModel | Mapping[str, object]) -> None:
    """Write a JSON artifact in the pinned byte format.

    The format: `model_dump(mode="json")` for models, UTF-8, 2-space indent,
    keys in model-declaration (or mapping-insertion) order — no sorting;
    pydantic order is deterministic — and a trailing newline.

    Args:
        path: The destination file.
        artifact: A pydantic model, or an already-serialized mapping (osrlib's
            stamped `adventure.json` document is a plain dict).
    """
    data = artifact.model_dump(mode="json") if isinstance(artifact, BaseModel) else artifact
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")


class Workdir:
    """One conversion's working directory, owning the spec's layout.

    Attributes:
        root: The workdir root, e.g. `my-module.forge/`.
    """

    def __init__(self, root: Path) -> None:
        """Bind to a workdir root without touching the filesystem.

        Args:
            root: The workdir root directory.
        """
        self.root = root

    @property
    def source_pdf(self) -> Path:
        """The copied source module."""
        return self.root / "source.pdf"

    @property
    def run_json(self) -> Path:
        """The run metadata file."""
        return self.root / "run.json"

    @property
    def pages_dir(self) -> Path:
        """Per-page renders and text layers."""
        return self.root / "pages"

    @property
    def stages_dir(self) -> Path:
        """Cached raw model-stage outputs."""
        return self.root / "stages"

    @property
    def survey_json(self) -> Path:
        """The survey stage's cache."""
        return self.stages_dir / "survey.json"

    def areas_json(self, dungeon_id: str, level_number: int) -> Path:
        """Return the content stage's cache path for one level.

        Filename unambiguity is guaranteed by the canonical slug alphabet — no
        dots or slashes in dungeon ids.

        Args:
            dungeon_id: The canonical dungeon id.
            level_number: The 1-based level number.

        Returns:
            `stages/areas.<dungeon>.<level>.json`.
        """
        return self.stages_dir / f"areas.{dungeon_id}.{level_number}.json"

    @property
    def overrides_yaml(self) -> Path:
        """The human correction file."""
        return self.root / "overrides.yaml"

    @property
    def previews_dir(self) -> Path:
        """Rendered SVG level maps."""
        return self.root / "previews"

    @property
    def report_json(self) -> Path:
        """The extraction report."""
        return self.root / "report.json"

    @property
    def adventure_json(self) -> Path:
        """The stamped osrlib adventure document."""
        return self.root / "adventure.json"

    def page_png(self, page_number: int) -> Path:
        """Return the render path for a page.

        Args:
            page_number: The 1-based page number.

        Returns:
            `pages/NNNN.png`, zero-padded to 4 digits.
        """
        return self.pages_dir / f"{page_number:04d}.png"

    def page_txt(self, page_number: int) -> Path:
        """Return the text-layer path for a page.

        Args:
            page_number: The 1-based page number.

        Returns:
            `pages/NNNN.txt`, zero-padded to 4 digits.
        """
        return self.pages_dir / f"{page_number:04d}.txt"

    def area_caches(self) -> list[Path]:
        """Return every content-stage cache file, sorted.

        Only the per-level `areas.*.json` caches — not `survey.json` (or
        phase 2's `monsters.json`): both extraction stages clear exactly these
        when a re-run may have orphaned them.

        Returns:
            The `stages/areas.*.json` paths, sorted by name.
        """
        return sorted(self.stages_dir.glob("areas.*.json"))

    def read_run(self) -> RunMeta:
        """Load and validate `run.json`.

        Returns:
            The run metadata.
        """
        return RunMeta.model_validate_json(self.run_json.read_text(encoding="utf-8"))

    def write_run(self, run: RunMeta) -> None:
        """Write `run.json` in the pinned artifact format.

        Args:
            run: The run metadata to persist.
        """
        write_json_artifact(self.run_json, run)


class StageTracker:
    """Accumulates one tracked stage's token usage and model identity.

    Yielded by [`track_stage`][osrforge.workdir.track_stage]; stage functions
    call [`add_usage`][osrforge.workdir.StageTracker.add_usage] and
    [`set_model`][osrforge.workdir.StageTracker.set_model] once per provider
    response.
    """

    def __init__(self) -> None:
        """Start with zero usage and no model identity."""
        self.usage = TokenUsage()
        self.provider: str | None = None
        self.model_id: str | None = None

    def add_usage(self, usage: TokenUsage) -> None:
        """Accumulate one response's token usage.

        Call exactly once per provider response — `FoundryProvider` already
        folds schema-retry attempts into each response's usage, so summing
        anywhere else double-counts.

        Args:
            usage: The response's usage.
        """
        self.usage = self.usage + usage

    def set_model(self, provider: str, model_id: str) -> None:
        """Record the provider and model identity from a response.

        Last write wins — if a deployment updates mid-run, `run.json` records
        the most recent response's `model_id`.

        Args:
            provider: The provider class name (`type(provider).__name__`).
            model_id: The model identifier the service returned.
        """
        self.provider = provider
        self.model_id = model_id


@contextmanager
def track_stage(workdir: Workdir, stage: Stage) -> Generator[StageTracker]:
    """Own one stage's running → completed/failed choreography in `run.json`.

    On enter, writes the stage `running` with `started_at`. On clean exit,
    writes `completed` with `finished_at`, the accumulated usage, and — when
    the tracker saw a response — the run's provider/model identity. On
    exception, writes `failed` with `error=str(exc)`, the usage spent so far,
    and `finished_at`, then re-raises, leaving upstream artifacts untouched.

    Timestamps are legal here and only here — `run.json` is operational
    metadata, per phase 0's pin.

    Args:
        workdir: The workdir whose `run.json` records the stage.
        stage: The stage being run.

    Yields:
        The tracker the stage function feeds per-response usage and identity.
    """
    run = workdir.read_run()
    started_at = datetime.now(UTC)
    workdir.write_run(run.with_stage(stage, StageStatus(status="running", started_at=started_at)))
    tracker = StageTracker()
    try:
        yield tracker
    except Exception as error:
        status = StageStatus(
            status="failed",
            error=str(error),
            started_at=started_at,
            finished_at=datetime.now(UTC),
            usage=tracker.usage,
        )
        workdir.write_run(run.with_stage(stage, status))
        raise
    status = StageStatus(
        status="completed",
        started_at=started_at,
        finished_at=datetime.now(UTC),
        usage=tracker.usage,
    )
    run = run.with_stage(stage, status)
    if tracker.provider is not None and tracker.model_id is not None:
        run = run.with_model(tracker.provider, tracker.model_id)
    workdir.write_run(run)
