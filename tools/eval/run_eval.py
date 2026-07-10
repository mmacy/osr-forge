"""The eval harness driver: verify → convert (live), score (offline), report (offline).

Manual, repo-only wiring — never packaged, never in CI (the spec forbids
per-commit evals). Everything with behavior worth testing lives in
`osrforge.evals`; this file is argument parsing, provider construction, and
printing. See tools/eval/README.md for the corpus rules, the scoreboard, and
the regression rule.
"""

import argparse
import datetime
import sys
from pathlib import Path

from osrforge.contracts.run import RunMeta, TokenUsage
from osrforge.convert import convert
from osrforge.estimate import INPUT_USD_PER_TOKEN, OUTPUT_USD_PER_TOKEN
from osrforge.evals import (
    ModuleMetrics,
    ModuleScore,
    RunInfo,
    corpus_means,
    load_manifest,
    load_scoreboard,
    load_truth,
    save_scoreboard,
    score_workdir,
    verify_source,
)
from osrforge.settings import ConversionSettings
from osrforge.versioning import osrforge_version
from osrforge.workdir import Workdir

EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
SCOREBOARD = EVAL_DIR / "scoreboard.json"


def module_dir(module_id: str) -> Path:
    path = CORPUS_DIR / module_id
    if not path.is_dir():
        known = ", ".join(sorted(child.name for child in CORPUS_DIR.iterdir() if child.is_dir()))
        sys.exit(f"unknown corpus module {module_id!r} (known: {known})")
    return path


def cmd_convert(args: argparse.Namespace) -> None:
    manifest = load_manifest(module_dir(args.module_id) / "manifest.yaml")
    # The sha256 gate runs before provider construction — before any spend.
    verify_source(manifest, args.pdf)
    print(f"{args.pdf} matches the {args.module_id} manifest ({manifest.pages} pages expected)")
    from osrforge.providers.foundry import FoundryProvider, FoundrySettings

    provider = FoundryProvider(FoundrySettings.from_env())
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.module_id}.forge")
    result = convert(args.pdf, workdir, provider, ConversionSettings())
    usage = _run_usage(result.run)
    print(
        f"converted into {workdir}: in={usage.input_tokens} out={usage.output_tokens} "
        f"(~${_run_usd(result.run):.2f}); now: run_eval.py score {args.module_id} --workdir {workdir}"
    )


def _run_usage(run: RunMeta) -> TokenUsage:
    total = TokenUsage()
    for status in run.stages.values():
        if status.usage is not None:
            total = total + status.usage
    return total


def _run_usd(run: RunMeta) -> float:
    usage = _run_usage(run)
    return usage.input_tokens * INPUT_USD_PER_TOKEN + usage.output_tokens * OUTPUT_USD_PER_TOKEN


def _print_metrics(module_id: str, metrics: ModuleMetrics) -> None:
    def show(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    print(f"{module_id}:")
    print(
        f"  areas:       recall={show(metrics.areas.recall)} precision={show(metrics.areas.precision)} "
        f"({metrics.areas.matched}/{metrics.areas.truth_areas} truth, {metrics.areas.extracted_areas} extracted)"
    )
    print(
        f"  encounters:  name_recall={show(metrics.encounters.name_recall)} "
        f"count_accuracy={show(metrics.encounters.count_accuracy)} "
        f"resolution_accuracy={show(metrics.encounters.resolution_accuracy)} "
        f"(non_srd={metrics.encounters.non_srd})"
    )
    print(
        f"  connections: f1={show(metrics.connections.f1)} precision={show(metrics.connections.precision)} "
        f"recall={show(metrics.connections.recall)} "
        f"({metrics.connections.true_positives}/{metrics.connections.truth_edges} truth edges)"
    )
    print(
        f"  treasure:    presence_agreement={show(metrics.treasure.presence_agreement)} "
        f"letter_accuracy={show(metrics.treasure.letter_accuracy)}"
    )


def cmd_score(args: argparse.Namespace) -> None:
    truth = load_truth(module_dir(args.module_id) / "truth.yaml")
    metrics = score_workdir(args.workdir, truth)
    _print_metrics(args.module_id, metrics)
    if not args.update_scoreboard:
        return
    workdir = Workdir(args.workdir)
    run = workdir.read_run()
    usage = _run_usage(run)
    score = ModuleScore(
        run=RunInfo(
            date=datetime.date.today().isoformat(),
            model_id=run.model_id or "unknown",
            osrforge_version=osrforge_version(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            usd=round(_run_usd(run), 4),
        ),
        metrics=metrics,
    )
    scoreboard = load_scoreboard(SCOREBOARD)
    scoreboard = scoreboard.model_copy(
        update={"modules": dict(sorted({**scoreboard.modules, args.module_id: score}.items()))}
    )
    save_scoreboard(SCOREBOARD, scoreboard)
    print(f"updated {SCOREBOARD}")


def cmd_report(args: argparse.Namespace) -> None:
    scoreboard = load_scoreboard(SCOREBOARD)
    if not scoreboard.modules:
        sys.exit(f"no scoreboard at {SCOREBOARD} — run a sweep first (see tools/eval/README.md)")
    for module_id, score in scoreboard.modules.items():
        print(
            f"# {module_id} — {score.run.date}, {score.run.model_id}, osr-forge {score.run.osrforge_version}, "
            f"in={score.run.input_tokens} out={score.run.output_tokens} (${score.run.usd:.2f})"
        )
        _print_metrics(module_id, score.metrics)
    print("# corpus means")
    for name, value in corpus_means(scoreboard).items():
        print(f"  {name}: {'n/a' if value is None else f'{value:.4f}'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval.py", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    convert_parser = subcommands.add_parser("convert", help="verify the PDF against the manifest, then convert (live)")
    convert_parser.add_argument("module_id", help="the corpus module id (a directory under corpus/)")
    convert_parser.add_argument("pdf", type=Path, help="the locally downloaded module PDF")
    convert_parser.add_argument("--workdir", type=Path, default=None, help="the workdir (default: ./<module-id>.forge)")
    convert_parser.set_defaults(handler=cmd_convert)

    score_parser = subcommands.add_parser("score", help="score a converted workdir against the truth (offline)")
    score_parser.add_argument("module_id", help="the corpus module id")
    score_parser.add_argument("--workdir", type=Path, required=True, help="the converted workdir")
    score_parser.add_argument("--update-scoreboard", action="store_true", help="write the result into scoreboard.json")
    score_parser.set_defaults(handler=cmd_score)

    report_parser = subcommands.add_parser("report", help="render the committed scoreboard (offline)")
    report_parser.set_defaults(handler=cmd_report)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
