"""The `osrforge` console script: `convert`, `assemble`, and `preview`.

House-scale argparse; `check`, `rerun`, and `estimate` arrive in phase 3.
Runtime failures ([`OsrForgeError`][osrforge.errors.OsrForgeError] and pydantic
validation errors from malformed workdirs) render as a one-line message and
exit code 1; tracebacks are for bugs.
"""

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from osrforge.assemble import assemble, render_previews
from osrforge.convert import StageEvent, convert
from osrforge.errors import OsrForgeError
from osrforge.providers.base import ModelProvider
from osrforge.settings import ConversionSettings

__all__ = ["main"]


def _build_provider(name: str) -> ModelProvider:
    # Imported here so `assemble` and `preview` never touch the vendor SDK,
    # and tests can stub this seam without a real provider construction.
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


def _cmd_convert(args: argparse.Namespace) -> None:
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.pdf.stem}.forge")
    provider = _build_provider(args.provider)
    result = convert(args.pdf, workdir, provider, ConversionSettings(), on_progress=_print_event)
    validation = result.report.validation
    outcome = "passed" if validation.passed else f"failed ({len(validation.errors)} errors)"
    print(f"validation: {outcome}")
    print(f"wrote {workdir / 'adventure.json'}")


def _cmd_assemble(args: argparse.Namespace) -> None:
    result = assemble(args.workdir)
    validation = result.report.validation
    outcome = "passed" if validation.passed else f"failed ({len(validation.errors)} errors)"
    print(f"validation: {outcome}")
    print(f"wrote {args.workdir / 'adventure.json'}")


def _cmd_preview(args: argparse.Namespace) -> None:
    written = render_previews(args.workdir)
    for path in written:
        print(f"wrote {path}")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser — public so tests can drive parsing without a subprocess.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(prog="osrforge", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    convert_parser = subcommands.add_parser("convert", help="convert a module PDF into a draft adventure")
    convert_parser.add_argument("pdf", type=Path, help="the module PDF")
    convert_parser.add_argument("--workdir", type=Path, default=None, help="the workdir (default: ./<pdf-stem>.forge)")
    convert_parser.add_argument("--provider", choices=["foundry"], default="foundry", help="the model provider")
    convert_parser.set_defaults(handler=_cmd_convert)

    assemble_parser = subcommands.add_parser("assemble", help="stage caches → artifacts, pure")
    assemble_parser.add_argument(
        "--workdir", type=Path, default=Path("."), help="the workdir (default: the current directory)"
    )
    assemble_parser.set_defaults(handler=_cmd_assemble)

    preview_parser = subcommands.add_parser("preview", help="regenerate the SVG maps only")
    preview_parser.add_argument(
        "--workdir", type=Path, default=Path("."), help="the workdir (default: the current directory)"
    )
    preview_parser.set_defaults(handler=_cmd_preview)
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the CLI.

    Args:
        argv: Argument list, defaulting to `sys.argv[1:]`.
    """
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except (OsrForgeError, ValidationError, ValueError, FileNotFoundError) as error:
        sys.exit(f"osrforge: {error}")


if __name__ == "__main__":
    main()
