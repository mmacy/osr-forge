"""The `osrforge` console script: `convert`, `rerun`, `assemble`, `check`, `report`, `preview`, and `estimate`.

House-scale argparse. Runtime failures ([`OsrForgeError`][osrforge.errors.OsrForgeError],
pydantic validation errors from malformed workdirs or bad `--set` values, and
YAML syntax errors from a hand-edited `overrides.yaml`) render as a one-line
message and exit code 1; a `convert`/`rerun` failure whose workdir records a
failed stage appends the exact `osrforge rerun` command that resumes it.
Tracebacks are for bugs. `check` exits 1 exactly when validation failed or any
error-severity finding exists — warnings don't break the `assemble && check`
loop.
"""

import argparse
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from osrforge.assemble import assemble, render_previews
from osrforge.check import check
from osrforge.contracts.report import ExtractionReport, Flag, parse_flag
from osrforge.contracts.run import Stage
from osrforge.convert import RUNNABLE_STAGES, StageEvent, convert, rerun
from osrforge.errors import OsrForgeError
from osrforge.estimate import estimate
from osrforge.providers.base import ModelProvider
from osrforge.settings import ConversionSettings
from osrforge.versioning import osrforge_version
from osrforge.workdir import Workdir

__all__ = ["format_report", "main", "parse_set_values"]

OVERRIDES_TEMPLATE = """\
# osr-forge correction file — applied on every `osrforge assemble`.
#
# Areas are addressed as <dungeon-id>/<level-number>/<area-key>; geometry
# entries address a level, <dungeon-id>/<level-number>. Every entry carries a
# required, non-empty `reason` — corrections are reviewable decisions. Every
# entry must take effect: a key that matches nothing fails assembly loudly.
#
# monsters:
#   "hobgoblin chieftain":
#     template_id: hobgoblin
#     reason: No SRD template for the named chieftain; base hobgoblin is closest.
#
# monster_templates:
#   "tentacle worm":
#     ac: "3 [16]"
#     reason: The extracted stat block read AC 8; the page prints 3.
#
# areas:
#   barrow/1/7:
#     description: Corrected text copied from p. 14.
#     reason: Extraction merged rooms 7 and 8.
#   barrow/1/95:
#     remove: true
#     reason: A map-only artifact, not a real room.
#
# geometry:
#   barrow/1:
#     areas:
#       "7":
#         cells: [[4, 2], [5, 2], [4, 3], [5, 3]]
#     edges:
#       "5,2:east": { kind: door, door: { stuck: true } }
#     reason: Match the printed map; room 7 is 20' x 20' with a stuck east door.
#
# town:
#   name: Riverton
#   reason: The module names the base town on p. 3.
#
# module:
#   name: The Example Barrow
#   reason: Title page.
"""


def _build_provider(name: str) -> ModelProvider:
    # Imported here so the pure commands never touch the vendor SDK, and
    # tests can stub this seam without a real provider construction.
    from osrforge.providers.foundry import FoundryProvider, FoundrySettings

    assert name == "foundry", f"unknown provider {name!r}"  # argparse choices already enforce this
    return FoundryProvider(FoundrySettings.from_env())


def _print_event(event: StageEvent) -> None:
    if event.status == "running":
        print(f"{event.stage.value}: running")
        return
    suffix = ""
    if event.usage is not None and (event.usage.input_tokens or event.usage.output_tokens):
        suffix = f" (in={event.usage.input_tokens} out={event.usage.output_tokens})"
    print(f"{event.stage.value}: {event.status}{suffix}")


def parse_set_values(values: list[str] | None) -> dict[str, object]:
    """Parse repeated `--set KEY=VALUE` pairs; values coerce through YAML.

    `yaml.safe_load` makes `21`, `[21, 30]`, and `best-effort` all coerce
    naturally; pydantic validation against `ConversionSettings` happens in the
    library call. Shared with the eval harness driver, whose `convert` takes
    the same flag.

    Args:
        values: The raw `KEY=VALUE` strings, or None.

    Returns:
        Key → YAML-coerced value.

    Raises:
        ValueError: If an item is not of the form `KEY=VALUE`.
    """
    updates: dict[str, object] = {}
    for item in values or []:
        key, separator, raw = item.partition("=")
        if not separator or not key:
            raise ValueError(f"--set expects KEY=VALUE, got {item!r}")
        updates[key] = yaml.safe_load(raw)
    return updates


def _validation_line(passed: bool, error_count: int) -> str:
    outcome = "passed" if passed else f"failed ({error_count} errors)"
    return f"validation: {outcome}"


def _print_validation(report_validation_passed: bool, error_count: int) -> None:
    print(_validation_line(report_validation_passed, error_count))


def format_report(report: ExtractionReport) -> str:
    """Format the review summary `osrforge report` prints over a `report.json`.

    Presentation only — machine consumers keep reading the artifact. The
    sections mirror the review loop's reading order: validation, flags grouped
    by kind with their locations (module-scope entries at location `module`),
    the monster-resolution summary with the unresolved names, and the
    playability findings by severity.

    Args:
        report: The parsed report.

    Returns:
        The summary text, ending in a newline.
    """
    lines = [f"{report.module.title} — {report.module.pages} pages, osrforge {report.osrforge_version}"]
    lines.append(_validation_line(report.validation.passed, len(report.validation.errors)))
    lines.extend(f"  {error}" for error in report.validation.errors)

    grouped: dict[Flag, list[str]] = {}

    def group(flag_string: str, location: str) -> None:
        flag, detail = parse_flag(flag_string)
        grouped.setdefault(flag, []).append(location if detail is None else f"{location} ({detail})")

    for area in report.areas:
        for flag_string in area.flags:
            group(flag_string, area.id)
    for flag_string in report.flags:
        group(flag_string, "module")
    total = sum(len(entries) for entries in grouped.values())
    lines.append(f"flags: {total}" if grouped else "flags: none")
    for flag in Flag:
        entries = grouped.get(flag)
        if entries:
            lines.append(f"  {flag.value}: {', '.join(entries)}")

    summary = report.monsters
    lines.append(
        f"monsters: {summary.resolved} resolved, {len(summary.unresolved)} unresolved, "
        f"{len(summary.custom)} custom, {len(summary.vetoed)} vetoed"
    )
    if summary.unresolved:
        lines.append(f"  unresolved: {', '.join(summary.unresolved)}")
    if summary.custom:
        emitted = ", ".join(f"{record.id} ({record.name})" for record in summary.custom)
        lines.append(f"  custom: {emitted}")
    for vetoed in summary.vetoed:
        record = vetoed.detail or f"{vetoed.name} → {vetoed.vetoed_template_id}"
        lines.append(f"  vetoed: {record}")

    if report.findings:
        errors = [finding for finding in report.findings if finding.severity == "error"]
        warnings = [finding for finding in report.findings if finding.severity == "warning"]
        lines.append(f"findings: {len(errors)} errors, {len(warnings)} warnings")
        lines.extend(
            f"  {finding.severity} {finding.id.value} {finding.location} {finding.message}"
            for finding in (*errors, *warnings)
        )
    else:
        lines.append("findings: none recorded (osrforge check records them)")
    return "\n".join(lines) + "\n"


def _resolve_workdir(explicit: Path | None) -> Path:
    """Resolve the workdir: the explicit `--workdir` value, or discovery from the working directory.

    Discovery order: the working directory itself when it is a workdir
    (it contains `run.json` — the old `--workdir .` default, kept), else the
    unique `*.forge` directory within it. Ambiguity and absence are loud errors
    naming what was found.

    Args:
        explicit: The `--workdir` flag's value, or None to discover. An
            explicit flag bypasses discovery entirely.

    Returns:
        The workdir to use.

    Raises:
        OsrForgeError: If discovery finds multiple `*.forge` directories, or
            none while the working directory is not itself a workdir.
    """
    if explicit is not None:
        return explicit
    if Path("run.json").is_file():
        return Path(".")
    candidates = sorted(path for path in Path(".").glob("*.forge") if path.is_dir())
    if len(candidates) == 1:
        return candidates[0]
    here = Path.cwd()
    if candidates:
        found = ", ".join(path.name for path in candidates)
        raise OsrForgeError(f"multiple workdirs in {here}: {found} — pass --workdir")
    raise OsrForgeError(f"{here} is not a workdir (no run.json) and contains no *.forge directory — pass --workdir")


def _resume_hint(args: argparse.Namespace) -> str | None:
    """Return the exact command that resumes a failed conversion chain, or None.

    Only the chain commands (`convert`, `rerun`) record their resolved workdir
    on the namespace; the hint fires exactly when that workdir's `run.json`
    exists, parses, and records a failed stage. Geometry fails inside assembly
    and has no independent run, so its resume command is `rerun assemble`.

    Args:
        args: The parsed namespace, after the command handler raised.

    Returns:
        The `resume with: …` line, or None when no hint applies.
    """
    workdir = getattr(args, "resolved_workdir", None)
    if workdir is None:
        return None
    try:
        run = Workdir(workdir).read_run()
    except OSError, ValidationError:
        return None
    failed = {stage for stage, status in run.stages.items() if status.status == "failed"}
    if not failed:
        return None
    resume = next((stage for stage in RUNNABLE_STAGES if stage in failed), Stage.ASSEMBLE)
    return f"resume with: osrforge rerun {resume.value} --workdir {workdir}"


def _cmd_convert(args: argparse.Namespace) -> None:
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.pdf.stem}.forge")
    args.resolved_workdir = workdir
    provider = _build_provider(args.provider)
    settings = ConversionSettings.model_validate(parse_set_values(args.set))
    result = convert(args.pdf, workdir, provider, settings, on_progress=_print_event)
    validation = result.report.validation
    _print_validation(validation.passed, len(validation.errors))
    print(f"wrote {workdir / 'adventure.json'}")
    overrides_path = Workdir(workdir).overrides_yaml
    if not overrides_path.exists():
        # The human loop's on-ramp. The library convert() does not write it
        # (hosts own their correction UX), and assemble never does (overrides
        # are input; a pure function doesn't author its own inputs).
        overrides_path.write_text(OVERRIDES_TEMPLATE, encoding="utf-8")
        print(f"wrote {overrides_path} (commented template)")


def _cmd_rerun(args: argparse.Namespace) -> None:
    workdir = _resolve_workdir(args.workdir)
    args.resolved_workdir = workdir
    stage = Stage(args.stage)
    provider = _build_provider(args.provider) if stage is not Stage.ASSEMBLE else None
    result = rerun(
        workdir,
        stage,
        provider=provider,
        settings_updates=parse_set_values(args.set) or None,
        on_progress=_print_event,
    )
    validation = result.report.validation
    _print_validation(validation.passed, len(validation.errors))
    print(f"wrote {workdir / 'adventure.json'}")


def _cmd_assemble(args: argparse.Namespace) -> None:
    workdir = _resolve_workdir(args.workdir)
    result = assemble(workdir)
    validation = result.report.validation
    _print_validation(validation.passed, len(validation.errors))
    print(f"wrote {workdir / 'adventure.json'}")


def _cmd_check(args: argparse.Namespace) -> None:
    workdir = _resolve_workdir(args.workdir)
    findings = check(workdir)
    report = ExtractionReport.model_validate_json(Workdir(workdir).report_json.read_text(encoding="utf-8"))
    _print_validation(report.validation.passed, len(report.validation.errors))
    for finding in findings:
        print(f"{finding.severity} {finding.id.value} {finding.location} {finding.message}")
    if not report.validation.passed or any(finding.severity == "error" for finding in findings):
        sys.exit(1)


def _cmd_report(args: argparse.Namespace) -> None:
    workdir = _resolve_workdir(args.workdir)
    report_path = Workdir(workdir).report_json
    if not report_path.is_file():
        raise OsrForgeError(f"no report.json in {workdir} — run osrforge assemble first")
    report = ExtractionReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    # Presentation only: check owns the gating exit code; a failed validation
    # summarized here still exits 0.
    print(format_report(report), end="")


def _cmd_preview(args: argparse.Namespace) -> None:
    workdir = _resolve_workdir(args.workdir)
    written = render_previews(workdir)
    for path in written:
        print(f"wrote {path}")


def _cmd_estimate(args: argparse.Namespace) -> None:
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.pdf.stem}.forge")
    result = estimate(args.pdf, workdir)
    survey_note = f" ({result.survey_window_count} windows)" if result.survey_window_count > 1 else ""
    print(f"pages: {result.page_count}")
    print(f"page tokens: {result.text_tokens} text + {result.image_tokens} image")
    print(f"survey:   in={result.survey_input_tokens} out={result.survey_output_tokens}{survey_note}")
    print(f"census:   in={result.census_input_tokens} out={result.census_output_tokens}")
    print(f"content:  in={result.content_input_tokens} out={result.content_output_tokens}")
    print(f"monsters: in={result.monsters_input_tokens} out={result.monsters_output_tokens}")
    print(f"total:    in={result.input_tokens} out={result.output_tokens}")
    print(f"estimated cost: ${result.usd:.2f}")


def _add_workdir_option(parser: argparse.ArgumentParser, discover: bool) -> None:
    help_text = (
        "the workdir (default: discovered — the working directory if it is a workdir, "
        "else the unique *.forge directory in it)"
        if discover
        else "the workdir (default: ./<pdf-stem>.forge)"
    )
    parser.add_argument("--workdir", type=Path, default=None, help=help_text)


def _add_set_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="update a settings knob (repeatable); values parse as YAML",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser — public so tests can drive parsing without a subprocess.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(prog="osrforge", description=__doc__)
    parser.add_argument("--version", action="version", version=f"osrforge {osrforge_version()}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    convert_parser = subcommands.add_parser("convert", help="convert a module PDF into a draft adventure")
    convert_parser.add_argument("pdf", type=Path, help="the module PDF")
    _add_workdir_option(convert_parser, discover=False)
    convert_parser.add_argument("--provider", choices=["foundry"], default="foundry", help="the model provider")
    _add_set_option(convert_parser)
    convert_parser.set_defaults(handler=_cmd_convert)

    rerun_parser = subcommands.add_parser(
        "rerun", help="re-run one stage — and everything downstream of it — from cached upstream outputs"
    )
    rerun_parser.add_argument(
        "stage", choices=[stage.value for stage in RUNNABLE_STAGES], help="the stage to resume from"
    )
    _add_workdir_option(rerun_parser, discover=True)
    rerun_parser.add_argument("--provider", choices=["foundry"], default="foundry", help="the model provider")
    _add_set_option(rerun_parser)
    rerun_parser.set_defaults(handler=_cmd_rerun)

    assemble_parser = subcommands.add_parser("assemble", help="stage caches + overrides → artifacts, pure")
    _add_workdir_option(assemble_parser, discover=True)
    assemble_parser.set_defaults(handler=_cmd_assemble)

    check_parser = subcommands.add_parser("check", help="validate_adventure + the playability lint")
    _add_workdir_option(check_parser, discover=True)
    check_parser.set_defaults(handler=_cmd_check)

    report_parser = subcommands.add_parser("report", help="summarize report.json — the review loop's first read")
    _add_workdir_option(report_parser, discover=True)
    report_parser.set_defaults(handler=_cmd_report)

    preview_parser = subcommands.add_parser("preview", help="regenerate the SVG maps only")
    _add_workdir_option(preview_parser, discover=True)
    preview_parser.set_defaults(handler=_cmd_preview)

    estimate_parser = subcommands.add_parser("estimate", help="preprocess only; rough token/cost estimate")
    estimate_parser.add_argument("pdf", type=Path, help="the module PDF")
    _add_workdir_option(estimate_parser, discover=False)
    estimate_parser.set_defaults(handler=_cmd_estimate)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the CLI.

    Args:
        argv: Argument list, defaulting to `sys.argv[1:]`.
    """
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except (OsrForgeError, ValidationError, ValueError, FileNotFoundError, yaml.YAMLError) as error:
        message = f"osrforge: {error}"
        hint = _resume_hint(args)
        sys.exit(message if hint is None else f"{message}\n{hint}")


if __name__ == "__main__":
    main()
