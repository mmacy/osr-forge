"""The end-to-end conversion and its resume: `preprocess → survey → content → monsters → assemble`.

Each stage writes its own `run.json` status; a stage failure propagates after
its `failed` status is written, keeping everything upstream. `rerun` resumes:
it re-runs the named stage *and everything downstream* through assemble — the
one reading that leaves a workdir artifact-consistent, since every stage
already clears or supersedes its downstream caches. `on_progress` receives the
stage-transitions-and-usage stream the spec promises hosts, covering exactly
the stages the chain runs.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from osrlib.crawl.adventure import Adventure

from osrforge.assemble import AssembleResult, assemble
from osrforge.content import content
from osrforge.contracts.report import ExtractionReport
from osrforge.contracts.run import RunMeta, Stage, StageState, TokenUsage
from osrforge.monsters import monsters
from osrforge.preprocess import preprocess
from osrforge.providers.base import ModelProvider
from osrforge.settings import ConversionSettings
from osrforge.survey import survey
from osrforge.workdir import Workdir

__all__ = ["KNOB_STAGES", "RUNNABLE_STAGES", "ConversionResult", "OnProgress", "StageEvent", "convert", "rerun"]

RUNNABLE_STAGES: tuple[Stage, ...] = (Stage.PREPROCESS, Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS, Stage.ASSEMBLE)
"""The five chain steps, in order — geometry has no independent run (it lives inside assembly)."""

_MODEL_STAGES = frozenset({Stage.SURVEY, Stage.CONTENT, Stage.MONSTERS})

KNOB_STAGES: Mapping[str, Stage] = {
    "render_dpi": Stage.PREPROCESS,
    "max_pages": Stage.PREPROCESS,
    "max_source_bytes": Stage.PREPROCESS,
    "blank_page_renders": Stage.PREPROCESS,
    "survey_max_pages": Stage.SURVEY,
    "content_batch_pages": Stage.CONTENT,
    "monster_fuzzy_threshold": Stage.MONSTERS,
    "monster_llm_top_k": Stage.MONSTERS,
    "custom_monsters": Stage.MONSTERS,
    "unresolved_fallback": Stage.ASSEMBLE,
}
"""Each settings knob's owning stage — the drift guard's table.

Updating a knob whose owning stage is upstream of the rerun stage is rejected:
the drifted `run.json` echo would otherwise claim, say, pages were rendered at
a DPI they weren't.
"""


@dataclass(frozen=True)
class StageEvent:
    """One progress event: a stage transition, with usage on completion.

    Events cover the chain steps the run drives; the `geometry` stage tracked
    inside `assemble()` gets no separate event — from a host's perspective it
    is an implementation detail of assembly, and its status is still readable
    in `run.json`.
    """

    stage: Stage
    status: StageState
    usage: TokenUsage | None = None


OnProgress = Callable[[StageEvent], None]
"""The progress callback: called synchronously with each stage transition."""


@dataclass(frozen=True)
class ConversionResult:
    """`convert`'s and `rerun`'s return: the final run metadata, the draft, and its report."""

    run: RunMeta
    adventure: Adventure
    report: ExtractionReport


def _run_chain(
    workdir_files: Workdir,
    steps: Sequence[tuple[Stage, Callable[[], object]]],
    on_progress: OnProgress | None,
) -> ConversionResult:
    """Run the chain steps in order; the last step is always assemble."""

    def emit(stage: Stage, status: StageState) -> None:
        if on_progress is None:
            return
        usage = None
        if status != "running" and workdir_files.run_json.is_file():
            stage_status = workdir_files.read_run().stages.get(stage)
            if stage_status is not None:
                usage = stage_status.usage
        on_progress(StageEvent(stage=stage, status=status, usage=usage))

    assembled: AssembleResult | None = None
    for stage, step in steps:
        emit(stage, "running")
        try:
            result = step()
        except Exception:
            emit(stage, "failed")
            raise
        emit(stage, "completed")
        if isinstance(result, AssembleResult):
            assembled = result
    assert assembled is not None, "every chain ends at assemble"
    return ConversionResult(
        run=workdir_files.read_run(),
        adventure=assembled.adventure,
        report=assembled.report,
    )


def _chain_steps(
    workdir_files: Workdir,
    workdir: Path,
    stages: Sequence[Stage],
    provider: ModelProvider | None,
    preprocess_step: Callable[[], object] | None,
) -> list[tuple[Stage, Callable[[], object]]]:
    model_steps = {Stage.SURVEY: survey, Stage.CONTENT: content, Stage.MONSTERS: monsters}
    steps: list[tuple[Stage, Callable[[], object]]] = []
    for stage in stages:
        if stage is Stage.PREPROCESS:
            assert preprocess_step is not None
            steps.append((stage, preprocess_step))
        elif stage is Stage.ASSEMBLE:
            steps.append((stage, lambda: assemble(workdir)))
        else:
            assert provider is not None  # guarded by the callers
            step = model_steps[stage]
            steps.append((stage, lambda step=step, provider=provider: step(workdir_files, provider)))
    return steps


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
        ExtractionError: If the survey finds nothing.
        ProviderError: On provider transport, auth, or rate-limit exhaustion.
        SchemaValidationError: If the provider exhausts its schema budget.
        OverrideError: If an existing `overrides.yaml` entry cannot take effect.
    """
    workdir_files = Workdir(workdir)
    effective = settings if settings is not None else ConversionSettings()
    steps = _chain_steps(
        workdir_files,
        workdir,
        RUNNABLE_STAGES,
        provider,
        preprocess_step=lambda: preprocess(pdf_path, workdir, effective),
    )
    return _run_chain(workdir_files, steps, on_progress)


def rerun(
    workdir: Path,
    stage: Stage,
    provider: ModelProvider | None = None,
    settings_updates: Mapping[str, object] | None = None,
    on_progress: OnProgress | None = None,
) -> ConversionResult:
    """Re-run one stage — and everything downstream of it — from cached upstream outputs.

    The stage argument *is* the skip: the user names what to redo and
    everything upstream is kept verbatim (no automatic staleness detection).
    `rerun(…, Stage.ASSEMBLE)` needs no provider and is the documented
    correction-loop `assemble`; `rerun preprocess` reads the workdir's own
    `source.pdf`.

    Args:
        workdir: An existing workdir (its `run.json` must be present).
        stage: The stage to resume from — one of `RUNNABLE_STAGES` (geometry
            has no independent run; it lives inside assembly).
        provider: The model provider; required exactly when the resumed chain
            contains a model stage.
        settings_updates: Settings knobs to update in the `run.json` echo
            before the chain runs — the echo stays the single source of truth
            stages read. A knob owned by a stage upstream of `stage` is
            rejected: the drifted echo would lie about how upstream artifacts
            were produced.
        on_progress: The same stage-event stream `convert` emits, covering
            exactly the stages the resumed chain runs.

    Returns:
        The conversion result.

    Raises:
        ValueError: If `stage` is not runnable, the resumed chain needs a
            provider none was given for, a settings update targets an upstream
            stage, or a stage precondition fails (incomplete upstream).
        pydantic.ValidationError: If a settings update names an unknown knob
            or an invalid value.
    """
    if stage not in RUNNABLE_STAGES:
        runnable = ", ".join(member.value for member in RUNNABLE_STAGES)
        raise ValueError(f"stage {stage.value!r} has no independent run; choose one of: {runnable}")
    workdir_files = Workdir(workdir)
    run = workdir_files.read_run()
    start = RUNNABLE_STAGES.index(stage)
    resumed = RUNNABLE_STAGES[start:]
    model_stages = [member for member in resumed if member in _MODEL_STAGES]
    if provider is None and model_stages:
        names = ", ".join(member.value for member in model_stages)
        raise ValueError(f"rerun from {stage.value!r} runs the model stages {names} — a provider is required")

    settings = run.settings
    if settings_updates:
        settings = ConversionSettings.model_validate({**run.settings.model_dump(), **settings_updates})
        for key in settings_updates:
            owner = KNOB_STAGES[key]
            if RUNNABLE_STAGES.index(owner) < start:
                raise ValueError(
                    f"setting {key!r} belongs to the {owner.value} stage, upstream of {stage.value} — "
                    f"rerun {owner.value} instead"
                )

    if stage is Stage.PREPROCESS:
        # preprocess writes a fresh RunMeta itself (all stages pending — honest,
        # since everything downstream re-runs anyway) with these settings.
        preprocess_step = lambda: preprocess(workdir_files.source_pdf, workdir, settings)  # noqa: E731
    else:
        preprocess_step = None
        if settings_updates:
            workdir_files.write_run(run.model_copy(update={"settings": settings}))

    steps = _chain_steps(workdir_files, workdir, resumed, provider, preprocess_step)
    return _run_chain(workdir_files, steps, on_progress)
