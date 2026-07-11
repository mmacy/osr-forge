# osr-forge

Convert tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents: an LLM-assisted extraction pipeline, deterministic map-geometry synthesis, a human correction loop built on an overrides file, and validation against the real osrlib models.

Standalone package + CLI. Consumers need only its artifacts — `adventure.json`, `report.json`, `overrides.yaml`, SVG map previews — or its CLI, regardless of their own tech stack.

**Status:** released — install from PyPI, docs at <https://mmacy.github.io/osr-forge/>:

```sh
uv pip install osr-forge     # or: pip install osr-forge
```

The package runs the whole pipeline — preprocessing, the survey (chunked past `survey_max_pages`) and content extraction stages, monster resolution against the osrlib catalog, deterministic geometry synthesis, and assembly — plus the human correction loop (overrides application, `rerun`/resume, the playability lint with its smoke delve, cost estimation) and the eval harness that keeps extraction quality a measured number (`tools/eval/`). The roadmap's phase plans live in [docs/](docs/spec.md) beside [the specification](docs/spec.md).

The library entry points are `convert()`, `assemble()`, `check()`, and `estimate()`; the `osrforge` console script wraps them:

```sh
osrforge estimate my-module.pdf           # preprocess only; rough token/cost estimate
osrforge convert my-module.pdf            # full pipeline into ./my-module.forge
osrforge assemble --workdir my-module.forge   # stage caches + overrides → artifacts, pure
osrforge check --workdir my-module.forge      # validate_adventure + the playability lint
osrforge preview --workdir my-module.forge    # regenerate the SVG maps only
osrforge rerun assemble --workdir my-module.forge   # resume any stage through assemble
```

Recording sessions and live verification runs are driven via `tools/extract/run_extraction.py` (see [tools/extract/README.md](tools/extract/README.md)); the on-demand eval harness — corpus, scorer, and scoreboard — lives in `tools/eval/` (see [tools/eval/README.md](tools/eval/README.md)).

## The correction loop

Conversion produces a *draft* — every gap and guess called out in `report.json` — and corrections live in `overrides.yaml`, never in hand-edits to generated output (a fresh `convert` leaves a commented template there). The loop:

1. Read `report.json` (flags, findings, the monsters summary) and eyeball `previews/*.svg` against the printed map.
2. Edit `overrides.yaml`: monster remaps, per-area field replacement, area adds and removes, geometry (cells, edges, entrance, transitions), town/module metadata. Every entry carries a `reason`, and every entry must take effect — a typo'd address fails assembly loudly instead of silently doing nothing.
3. `osrforge assemble && osrforge check` — re-assembly is pure and instant (no model calls), and `check` exits 0 once validation passes and no error-severity finding remains.
4. Repeat until publishable.

Settings changes on an existing workdir go through `rerun --set` — for example, `osrforge rerun preprocess --set 'blank_page_renders=[21]'` blanks a render Azure's content filter rejects, or `osrforge rerun assemble --set unresolved_fallback=omit` flips the stand-in policy without re-rolling the model.

## Development quickstart

Requires Python ≥ 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
```

The full check suite, as CI runs it:

```sh
uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest
```

Tests use no network — model interactions replay from recorded fixtures. The only live-network activity in the repo is manual: the Foundry capability spike (`tools/spike/`) and the extraction runner (`tools/extract/`).

## Pipeline settings

`ConversionSettings` holds the deterministic knobs, echoed into each workdir's `run.json`:

| Knob | Default | Meaning |
| --- | --- | --- |
| `render_dpi` | 150 | Page-render resolution (a legibility knob, not a cost knob — see `docs/foundry-capabilities.md`) |
| `max_pages` | 200 | Source page-count guardrail |
| `max_source_bytes` | 100 MiB | Source file-size guardrail |
| `blank_page_renders` | `()` | Page numbers whose renders are emitted as blank white PNGs (text layer still extracted) — the content-safety-filter workaround; each blanked page is flagged `page_unreadable` in the report |
| `content_batch_pages` | 8 | Content-pass batch size in pages (floor 2) |
| `survey_max_pages` | 50 | The survey chunk size — the service's measured 50-images-per-request cap: a source at or under it surveys in one request; a larger source surveys in page windows of this size, merged before normalization |
| `monster_fuzzy_threshold` | 0.85 | Monster resolution's fuzzy-tier auto-accept floor, pinned against measured catalog pairs |
| `monster_llm_top_k` | 8 | Candidate templates offered per name in the monster-resolution LLM tier |
| `unresolved_fallback` | `best-effort` | Where resolution or parsing came up empty: flagged level-band monster stand-ins and unguarded-treasure rolls (`best-effort`), or leave the gap (`omit`) |

On an existing workdir, change a knob with `rerun --set KEY=VALUE`: the update lands in the `run.json` settings echo before the chain runs, and a knob owned by a stage upstream of the rerun stage is rejected with the stage to rerun instead.

## Provider configuration

The Azure AI Foundry adapter reads its connection from `OSRFORGE_FOUNDRY_*` environment variables (an osr-forge-specific prefix, deliberately not `AZURE_OPENAI_*`, to avoid colliding with other tools' conventions):

| Variable | Azure meaning | Required |
| --- | --- | --- |
| `OSRFORGE_FOUNDRY_ENDPOINT` | The Azure OpenAI resource endpoint (`https://<resource>.openai.azure.com`) | yes |
| `OSRFORGE_FOUNDRY_DEPLOYMENT` | The model deployment name | yes |
| `OSRFORGE_FOUNDRY_API_KEY` | The API key. Omit to use Entra ID via `DefaultAzureCredential`, which needs the `osr-forge[entra]` extra | no |

## Licensing

Package code is MIT (see [LICENSE](LICENSE)). osr-forge ships no game content: osrlib's OGL data stays in osrlib, and test assets live under `tests/assets/` outside the built distribution, each with its provenance and license documented in [tests/assets/README.md](tests/assets/README.md).

These fences govern what this repository redistributes, not what you convert: bringing your own purchased, non-redistributable module is the primary use case. Conversion runs locally and everything derived from your module stays in your own workdir — nothing is shared unless you share it.
