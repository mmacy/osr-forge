"""The `osrforge` console script: `convert`, `rerun`, `assemble`, `check`, `preview`, and `estimate`.

House-scale argparse. Runtime failures ([`OsrForgeError`][osrforge.errors.OsrForgeError],
pydantic validation errors from malformed workdirs or bad `--set` values, and
YAML syntax errors from a hand-edited `overrides.yaml`) render as a one-line
message and exit code 1; tracebacks are for bugs. `check` exits 1 exactly when
validation failed or any error-severity finding exists — warnings don't break
the `assemble && check` loop.
"""

import argparse
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from osrforge.assemble import assemble, render_previews
from osrforge.check import check
from osrforge.contracts.report import ExtractionReport
from osrforge.contracts.run import Stage
from osrforge.convert import RUNNABLE_STAGES, StageEvent, convert, rerun
from osrforge.errors import OsrForgeError
from osrforge.estimate import estimate
from osrforge.providers.base import ModelProvider
from osrforge.settings import ConversionSettings
from osrforge.versioning import osrforge_version
from osrforge.workdir import Workdir

__all__ = ["main", "parse_set_values"]

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


def _print_validation(report_validation_passed: bool, error_count: int) -> None:
    outcome = "passed" if report_validation_passed else f"failed ({error_count} errors)"
    print(f"validation: {outcome}")


def _cmd_convert(args: argparse.Namespace) -> None:
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.pdf.stem}.forge")
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
    stage = Stage(args.stage)
    provider = _build_provider(args.provider) if stage is not Stage.ASSEMBLE else None
    result = rerun(
        args.workdir,
        stage,
        provider=provider,
        settings_updates=parse_set_values(args.set) or None,
        on_progress=_print_event,
    )
    validation = result.report.validation
    _print_validation(validation.passed, len(validation.errors))
    print(f"wrote {args.workdir / 'adventure.json'}")


def _cmd_assemble(args: argparse.Namespace) -> None:
    result = assemble(args.workdir)
    validation = result.report.validation
    _print_validation(validation.passed, len(validation.errors))
    print(f"wrote {args.workdir / 'adventure.json'}")


def _cmd_check(args: argparse.Namespace) -> None:
    findings = check(args.workdir)
    report = ExtractionReport.model_validate_json(Workdir(args.workdir).report_json.read_text(encoding="utf-8"))
    _print_validation(report.validation.passed, len(report.validation.errors))
    for finding in findings:
        print(f"{finding.severity} {finding.id.value} {finding.location} {finding.message}")
    if not report.validation.passed or any(finding.severity == "error" for finding in findings):
        sys.exit(1)


def _cmd_preview(args: argparse.Namespace) -> None:
    written = render_previews(args.workdir)
    for path in written:
        print(f"wrote {path}")


def _cmd_estimate(args: argparse.Namespace) -> None:
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.pdf.stem}.forge")
    result = estimate(args.pdf, workdir)
    survey_note = f" ({result.survey_window_count} windows)" if result.survey_window_count > 1 else ""
    print(f"pages: {result.page_count}")
    print(f"page tokens: {result.text_tokens} text + {result.image_tokens} image")
    print(f"survey:   in={result.survey_input_tokens} out={result.survey_output_tokens}{survey_note}")
    print(f"content:  in={result.content_input_tokens} out={result.content_output_tokens}")
    print(f"monsters: in={result.monsters_input_tokens} out={result.monsters_output_tokens}")
    print(f"total:    in={result.input_tokens} out={result.output_tokens}")
    print(f"estimated cost: ${result.usd:.2f}")


def _add_workdir_option(parser: argparse.ArgumentParser, default: Path | None) -> None:
    help_text = (
        "the workdir (default: ./<pdf-stem>.forge)"
        if default is None
        else "the workdir (default: the current directory)"
    )
    parser.add_argument("--workdir", type=Path, default=default, help=help_text)


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
    _add_workdir_option(convert_parser, default=None)
    convert_parser.add_argument("--provider", choices=["foundry"], default="foundry", help="the model provider")
    _add_set_option(convert_parser)
    convert_parser.set_defaults(handler=_cmd_convert)

    rerun_parser = subcommands.add_parser(
        "rerun", help="re-run one stage — and everything downstream of it — from cached upstream outputs"
    )
    rerun_parser.add_argument(
        "stage", choices=[stage.value for stage in RUNNABLE_STAGES], help="the stage to resume from"
    )
    _add_workdir_option(rerun_parser, default=Path("."))
    rerun_parser.add_argument("--provider", choices=["foundry"], default="foundry", help="the model provider")
    _add_set_option(rerun_parser)
    rerun_parser.set_defaults(handler=_cmd_rerun)

    assemble_parser = subcommands.add_parser("assemble", help="stage caches + overrides → artifacts, pure")
    _add_workdir_option(assemble_parser, default=Path("."))
    assemble_parser.set_defaults(handler=_cmd_assemble)

    check_parser = subcommands.add_parser("check", help="validate_adventure + the playability lint")
    _add_workdir_option(check_parser, default=Path("."))
    check_parser.set_defaults(handler=_cmd_check)

    preview_parser = subcommands.add_parser("preview", help="regenerate the SVG maps only")
    _add_workdir_option(preview_parser, default=Path("."))
    preview_parser.set_defaults(handler=_cmd_preview)

    estimate_parser = subcommands.add_parser("estimate", help="preprocess only; rough token/cost estimate")
    estimate_parser.add_argument("pdf", type=Path, help="the module PDF")
    _add_workdir_option(estimate_parser, default=None)
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
        sys.exit(f"osrforge: {error}")


if __name__ == "__main__":
    main()
