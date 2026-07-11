"""The eval harness driver: verify → convert (live), score/report/publish (offline).

Manual, repo-only wiring — never packaged, never in CI (the spec forbids
per-commit evals). Everything with behavior worth testing lives in
`osrforge.evals`; this file is argument parsing, provider construction, and
printing. See tools/eval/README.md for the corpus rules, the scoreboard, and
the regression rule, and tools/eval/AUTHORING.md for the truth-authoring
discipline private (BYOM) corpora follow.

Every subcommand takes `--corpus DIR` (default: the repo's committed corpus).
A private corpus uses the identical layout — `<module-id>/manifest.yaml` +
`truth.yaml` — and one uniform scoreboard rule: every corpus's scoreboard is
`<corpus-dir>/scoreboard.json`.
"""

import argparse
import datetime
import hashlib
import sys
from pathlib import Path

from osrforge.cli import parse_set_values
from osrforge.contracts.run import RunMeta, TokenUsage
from osrforge.convert import convert
from osrforge.estimate import INPUT_USD_PER_TOKEN, OUTPUT_USD_PER_TOKEN
from osrforge.evals import (
    ModuleMetrics,
    ModuleScore,
    RunInfo,
    Scoreboard,
    corpus_means,
    enforce_source_integrity,
    load_byom_scoreboard,
    load_manifest,
    load_scoreboard,
    load_truth,
    publish_module,
    save_byom_scoreboard,
    save_scoreboard,
    score_workdir,
    settings_overrides,
    verify_source,
)
from osrforge.settings import ConversionSettings
from osrforge.versioning import osrforge_version
from osrforge.workdir import Workdir

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS = EVAL_DIR / "corpus"
BYOM_SCOREBOARD = EVAL_DIR / "byom-scoreboard.json"


def module_dir(corpus: Path, module_id: str) -> Path:
    path = corpus / module_id
    if not path.is_dir():
        known = ", ".join(sorted(child.name for child in corpus.iterdir() if child.is_dir())) or "none"
        sys.exit(f"unknown corpus module {module_id!r} in {corpus} (known: {known})")
    return path


def scoreboard_path(corpus: Path) -> Path:
    return corpus / "scoreboard.json"


def cmd_convert(args: argparse.Namespace) -> None:
    member = module_dir(args.corpus, args.module_id)
    manifest = load_manifest(member / "manifest.yaml")
    # The integrity gate runs before provider construction — before any spend.
    seeded = verify_source(manifest, member, args.pdf)
    if seeded:
        print(f"seeded {member / 'source.sha256'} — later runs must score this exact file")
    else:
        print(f"{args.pdf} matches the {args.module_id} integrity gate ({manifest.pages} pages expected)")
    settings = ConversionSettings.model_validate(parse_set_values(args.set))
    from osrforge.providers.foundry import FoundryProvider, FoundrySettings

    provider = FoundryProvider(FoundrySettings.from_env())
    workdir: Path = args.workdir if args.workdir is not None else Path(f"./{args.module_id}.forge")
    result = convert(args.pdf, workdir, provider, settings)
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
    # Base-tier pricing over the aggregated usage: per-request tiers are not
    # reconstructable post hoc, and every v1 corpus member sits far under the
    # 272K cliff. Run-metadata context, not a metric.
    usage = _run_usage(run)
    return usage.input_tokens * INPUT_USD_PER_TOKEN + usage.output_tokens * OUTPUT_USD_PER_TOKEN


def _print_metrics(module_id: str, metrics: ModuleMetrics) -> None:
    def show(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.4f}"

    print(f"{module_id}:")
    print(
        f"  areas:       recall={show(metrics.areas.recall)} precision={show(metrics.areas.precision)} "
        f"({metrics.areas.matched}/{metrics.areas.truth_areas} truth, {metrics.areas.extracted_areas} extracted; "
        f"dungeons {metrics.areas.matched_dungeons}/{metrics.areas.truth_dungeons} truth, "
        f"{metrics.areas.extracted_dungeons} extracted)"
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
    member = module_dir(args.corpus, args.module_id)
    manifest = load_manifest(member / "manifest.yaml")
    truth = load_truth(member / "truth.yaml")
    workdir = Workdir(args.workdir)
    run = workdir.read_run()
    # The chain of custody: the workdir must hold the file the truth was
    # authored against, or its scores are noise. A workdir converted outside
    # the harness seeds the sidecar here, at first score.
    seeded = enforce_source_integrity(
        manifest, member, run.source_sha256, f"{args.workdir} (run.json's recorded source)"
    )
    if seeded:
        print(f"seeded {member / 'source.sha256'} — later runs must score this exact file")
    metrics = score_workdir(args.workdir, truth)
    _print_metrics(args.module_id, metrics)
    if not args.update_scoreboard:
        return
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
        settings_overrides=settings_overrides(run.settings),
        metrics=metrics,
    )
    board_path = scoreboard_path(args.corpus)
    scoreboard = load_scoreboard(board_path)
    # Reconstruct rather than model_copy: construction runs the key-sorting validator.
    scoreboard = Scoreboard(
        schema_version=scoreboard.schema_version,
        modules={**scoreboard.modules, args.module_id: score},
    )
    save_scoreboard(board_path, scoreboard)
    print(f"updated {board_path}")


def cmd_report(args: argparse.Namespace) -> None:
    if args.byom:
        _report_byom()
        return
    board_path = scoreboard_path(args.corpus)
    scoreboard = load_scoreboard(board_path)
    if not scoreboard.modules:
        sys.exit(f"no scoreboard at {board_path} — run a sweep first (see tools/eval/README.md)")
    for module_id, score in scoreboard.modules.items():
        print(
            f"# {module_id} — {score.run.date}, {score.run.model_id}, osr-forge {score.run.osrforge_version}, "
            f"in={score.run.input_tokens} out={score.run.output_tokens} (${score.run.usd:.2f})"
        )
        if score.settings_overrides:
            print(f"  overrides:   {' '.join(score.settings_overrides)}")
        _print_metrics(module_id, score.metrics)
    print("# corpus means")
    for name, value in corpus_means(scoreboard).items():
        print(f"  {name}: {'n/a' if value is None else f'{value:.4f}'}")


def _report_byom() -> None:
    board = load_byom_scoreboard(BYOM_SCOREBOARD)
    if not board.modules:
        sys.exit(f"no BYOM scoreboard at {BYOM_SCOREBOARD} — publish a module first (see tools/eval/AUTHORING.md)")
    for module_id, entry in board.modules.items():
        identity = ", ".join(part for part in (entry.publisher, entry.edition) if part)
        identity = f" ({identity})" if identity else ""
        print(f"# {module_id} — {entry.title}{identity}, {entry.pages} pages")
        print(
            f"  run: {entry.run.date}, {entry.run.model_id}, osr-forge {entry.run.osrforge_version}, "
            f"in={entry.run.input_tokens} out={entry.run.output_tokens} (${entry.run.usd:.2f})"
        )
        print(f"  truth: sha256 {entry.truth_sha256[:16]}…")
        if entry.settings_overrides:
            print(f"  overrides: {' '.join(entry.settings_overrides)}")
        _print_metrics(module_id, entry.metrics)


def cmd_publish(args: argparse.Namespace) -> None:
    member = module_dir(args.corpus, args.module_id)
    manifest = load_manifest(member / "manifest.yaml")
    truth_sha256 = hashlib.sha256((member / "truth.yaml").read_bytes()).hexdigest()
    private_board = load_scoreboard(scoreboard_path(args.corpus))
    committed_ids = {child.name for child in DEFAULT_CORPUS.iterdir() if child.is_dir()}
    board = publish_module(
        board=load_byom_scoreboard(BYOM_SCOREBOARD),
        module_id=args.module_id,
        manifest=manifest,
        private_board=private_board,
        truth_sha256=truth_sha256,
        committed_ids=committed_ids,
    )
    save_byom_scoreboard(BYOM_SCOREBOARD, board)
    print(f"published {args.module_id} ({manifest.title}) to {BYOM_SCOREBOARD}")


def _add_corpus_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="the corpus directory (default: the repo's committed corpus)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval.py", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    convert_parser = subcommands.add_parser("convert", help="verify the PDF's integrity, then convert (live)")
    convert_parser.add_argument("module_id", help="the corpus module id (a directory under the corpus)")
    convert_parser.add_argument("pdf", type=Path, help="the locally downloaded module PDF")
    convert_parser.add_argument("--workdir", type=Path, default=None, help="the workdir (default: ./<module-id>.forge)")
    convert_parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="update a settings knob (repeatable); values parse as YAML",
    )
    _add_corpus_option(convert_parser)
    convert_parser.set_defaults(handler=cmd_convert)

    score_parser = subcommands.add_parser("score", help="score a converted workdir against the truth (offline)")
    score_parser.add_argument("module_id", help="the corpus module id")
    score_parser.add_argument("--workdir", type=Path, required=True, help="the converted workdir")
    score_parser.add_argument(
        "--update-scoreboard", action="store_true", help="write the result into the corpus's scoreboard.json"
    )
    _add_corpus_option(score_parser)
    score_parser.set_defaults(handler=cmd_score)

    report_parser = subcommands.add_parser("report", help="render a corpus scoreboard (offline)")
    report_parser.add_argument("--byom", action="store_true", help="render the committed BYOM scoreboard instead")
    _add_corpus_option(report_parser)
    report_parser.set_defaults(handler=cmd_report)

    publish_parser = subcommands.add_parser(
        "publish", help="copy a private corpus module's scored entry onto the committed BYOM scoreboard (offline)"
    )
    publish_parser.add_argument("module_id", help="the private corpus module id")
    _add_corpus_option(publish_parser)
    publish_parser.set_defaults(handler=cmd_publish)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
