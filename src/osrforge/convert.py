"""The end-to-end conversion: `preprocess → survey → content → monsters → assemble`.

Each stage already writes its own `run.json` status; a stage failure propagates
after its `failed` status is written, keeping everything upstream (the spec's
resume story, with `rerun` arriving in phase 3). `on_progress` receives the
stage-transitions-and-usage stream the spec promises hosts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from osrlib.crawl.adventure import Adventure

from osrforge.assemble import assemble
from osrforge.content import content
from osrforge.contracts.report import ExtractionReport
from osrforge.contracts.run import RunMeta, Stage, StageState, TokenUsage
from osrforge.monsters import monsters
from osrforge.preprocess import preprocess
from osrforge.providers.base import ModelProvider
from osrforge.settings import ConversionSettings
from osrforge.survey import survey
from osrforge.workdir import Workdir

__all__ = ["ConversionResult", "OnProgress", "StageEvent", "convert"]


@dataclass(frozen=True)
class StageEvent:
    """One progress event: a stage transition, with usage on completion.

    Events cover the five chain steps `convert` drives; the `geometry` stage
    tracked inside `assemble()` gets no separate event — from a host's
    perspective it is an implementation detail of assembly, and its status is
    still readable in `run.json`.
    """

    stage: Stage
    status: StageState
    usage: TokenUsage | None = None


OnProgress = Callable[[StageEvent], None]
"""The progress callback: called synchronously with each stage transition."""


@dataclass(frozen=True)
class ConversionResult:
    """`convert`'s return: the final run metadata, the draft, and its report."""

    run: RunMeta
    adventure: Adventure
    report: ExtractionReport


def convert(
    pdf_path: Path,
    workdir: Path,
    provider: ModelProvider,
    settings: ConversionSettings | None = None,
    on_progress: OnProgress | None = None,
) -> ConversionResult:
    """Convert a module PDF into a draft adventure, end to end.

    Args:
        pdf_path: The source module PDF.
        workdir: The workdir root to create or rebuild.
        provider: The model provider for the extraction stages.
        settings: Pipeline settings; defaults to `ConversionSettings()`.
        on_progress: Optional callback receiving a `running` event before each
            stage and a `completed` event (with that stage's usage from
            `run.json`) after it; a failing stage emits `failed` before the
            error propagates.

    Returns:
        The conversion result.

    Raises:
        PdfError: If preprocessing rejects the source.
        ExtractionError: If the survey guard trips or the survey finds nothing.
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
    """
    workdir_files = Workdir(workdir)

    def emit(stage: Stage, status: StageState) -> None:
        if on_progress is None:
            return
        usage = None
        if status != "running" and workdir_files.run_json.is_file():
            stage_status = workdir_files.read_run().stages.get(stage)
            if stage_status is not None:
                usage = stage_status.usage
        on_progress(StageEvent(stage=stage, status=status, usage=usage))

    def run_stage[T](stage: Stage, step: Callable[[], T]) -> T:
        emit(stage, "running")
        try:
            result = step()
        except Exception:
            emit(stage, "failed")
            raise
        emit(stage, "completed")
        return result

    run_stage(Stage.PREPROCESS, lambda: preprocess(pdf_path, workdir, settings or ConversionSettings()))
    run_stage(Stage.SURVEY, lambda: survey(workdir_files, provider))
    run_stage(Stage.CONTENT, lambda: content(workdir_files, provider))
    run_stage(Stage.MONSTERS, lambda: monsters(workdir_files, provider))
    assembled = run_stage(Stage.ASSEMBLE, lambda: assemble(workdir))
    return ConversionResult(
        run=workdir_files.read_run(),
        adventure=assembled.adventure,
        report=assembled.report,
    )
