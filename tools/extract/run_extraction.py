"""Live extraction runs: preprocess → survey → content with the real Foundry adapter.

Manual, live-network, repo-only — never packaged, never in CI; see
tools/extract/README.md for the recording sessions. Recording is opt-in via
--record-fixtures: verification runs over non-redistributable modules must
leave no recorded module text behind.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, cast

from osrforge.content import build_batch_request, content, plan_content_batches
from osrforge.contracts.run import RunMeta, TokenUsage
from osrforge.pages import page_request_parts
from osrforge.preprocess import preprocess
from osrforge.providers.base import ModelProvider
from osrforge.providers.fixtures import RecordingProvider
from osrforge.providers.foundry import FoundryProvider, FoundrySettings
from osrforge.settings import ConversionSettings
from osrforge.survey import build_survey_request, filter_index_to_pages, normalize_survey, survey
from osrforge.workdir import Workdir

# Azure OpenAI GlobalStandard, <=272K-token requests, per docs/foundry-capabilities.md.
INPUT_USD_PER_TOKEN = 2.50 / 1_000_000
OUTPUT_USD_PER_TOKEN = 15.00 / 1_000_000


def make_provider(record_dir: Path | None) -> ModelProvider:
    provider: ModelProvider = FoundryProvider(FoundrySettings.from_env())
    if record_dir is not None:
        provider = RecordingProvider(provider, record_dir)
    return provider


def cost(usage: TokenUsage) -> float:
    return usage.input_tokens * INPUT_USD_PER_TOKEN + usage.output_tokens * OUTPUT_USD_PER_TOKEN


def print_run_summary(run: RunMeta) -> None:
    print(f"source sha256: {run.source_sha256}")
    print(f"page count:    {run.page_count}")
    total = TokenUsage()
    for stage, status in run.stages.items():
        if status.usage is not None and (status.usage.input_tokens or status.usage.output_tokens):
            total = total + status.usage
            print(
                f"{stage.value}: {status.status}, in={status.usage.input_tokens} "
                f"out={status.usage.output_tokens} (~${cost(status.usage):.2f})"
            )
    print(f"model: {run.model_id} via {run.provider}")
    print(f"total usage: in={total.input_tokens} out={total.output_tokens} (~${cost(total):.2f})")


def cmd_full(args: argparse.Namespace) -> None:
    provider = make_provider(args.record_fixtures)
    workdir = Workdir(args.workdir)
    run = preprocess(args.pdf, args.workdir, ConversionSettings())
    print(f"preprocessed {run.page_count} pages into {workdir.root}")
    index = survey(workdir, provider)
    print(
        f'survey: "{index.title}", {len(index.hooks)} hooks, town "{index.town.name}", '
        f"{len(index.monster_names)} monster names"
    )
    for dungeon in index.dungeons:
        for level in dungeon.levels:
            print(f"  {dungeon.id}/{level.number}: {len(level.areas)} areas, map pages {list(level.map_pages)}")
    levels = content(workdir, provider)
    for level_content in levels:
        extracted = len(level_content.areas)
        with_pages = sum(1 for area in level_content.areas if area.source_pages)
        print(
            f"content {level_content.dungeon_id}/{level_content.level_number}: "
            f"{extracted} areas extracted, {with_pages} with source pages"
        )
    print_run_summary(workdir.read_run())


def cmd_excerpt(args: argparse.Namespace) -> None:
    # The committed page subset keeps its original page numbering (0008,
    # 0022, ...), so a Workdir bound to the asset directory serves the pages
    # without any fabricated workdir; page parts come exclusively from
    # tests/assets/<module>/pages/, exactly as the replay test rebuilds them.
    asset_workdir = Workdir(args.module_dir)
    pages = sorted(int(path.stem) for path in asset_workdir.pages_dir.glob("*.png"))
    if not pages:
        sys.exit(f"no committed pages in {asset_workdir.pages_dir}")
    provider = make_provider(args.record_fixtures)
    print(f"excerpt survey over committed pages {pages} (module page count {args.page_count})")
    response = provider.generate(build_survey_request(page_request_parts(asset_workdir, pages)))
    print(f"survey usage: in={response.usage.input_tokens} out={response.usage.output_tokens}")
    index = normalize_survey(cast(dict[str, Any], response.data), args.page_count)
    # The pinned closure step: restrict every page reference to the committed
    # subset before planning, so an in-range reference to an uncommitted page
    # cannot make the batch request unbuildable at replay time.
    index = filter_index_to_pages(index, pages)
    plans = plan_content_batches(index, ConversionSettings().content_batch_pages)
    batch = next((batch for plan in plans for batch in plan.batches), None)
    if batch is None:
        sys.exit("the excerpt survey planned no content batches — nothing to record")
    print(f"first content batch: {batch.tag}, pages {list(batch.part_pages)}, {len(batch.areas)} areas")
    batch_response = provider.generate(build_batch_request(batch, page_request_parts(asset_workdir, batch.part_pages)))
    print(f"content usage: in={batch_response.usage.input_tokens} out={batch_response.usage.output_tokens}")
    data = cast(dict[str, Any], batch_response.data)
    print(f"content areas returned: {[entry['key'] for entry in data['areas']]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    full = subcommands.add_parser("full", help="preprocess, survey, and extract a whole module PDF")
    full.add_argument("pdf", type=Path, help="the module PDF")
    full.add_argument("--workdir", type=Path, required=True, help="the workdir to create or rebuild")
    full.add_argument(
        "--record-fixtures",
        type=Path,
        default=None,
        help="record every exchange as fixtures into this directory (opt-in; embeds module text)",
    )

    excerpt = subcommands.add_parser(
        "excerpt", help="record the replay-grade survey + first-content-batch chain over committed pages"
    )
    excerpt.add_argument(
        "--module-dir", type=Path, required=True, help="the fenced asset directory (with its pages/ subset)"
    )
    excerpt.add_argument(
        "--page-count", type=int, required=True, help="the real module's page count, for normalization"
    )
    excerpt.add_argument(
        "--record-fixtures",
        type=Path,
        required=True,
        help="the replay fixture directory (e.g. tests/assets/<module>/fixtures-extract/replay)",
    )

    args = parser.parse_args()
    {"full": cmd_full, "excerpt": cmd_excerpt}[args.command](args)


if __name__ == "__main__":
    main()
