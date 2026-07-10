# osr-forge

Convert tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents: an LLM-assisted extraction pipeline, deterministic map-geometry synthesis, a human correction loop built on an overrides file, and validation against the real osrlib models.

Standalone package + CLI. Consumers need only its artifacts — `adventure.json`, `report.json`, `overrides.yaml`, SVG map previews — or its CLI, regardless of their own tech stack.

**Status:** phase 2 (a playable draft). See [the specification](docs/spec.md) and the plans for [phase 0](docs/phase-0-plan.md), [phase 1](docs/phase-1-plan.md), and [phase 2](docs/phase-2-plan.md). The package runs the whole pipeline: preprocessing, the survey and content extraction stages, monster resolution against the osrlib catalog, deterministic geometry synthesis, and assembly producing `adventure.json`, `report.json`, and SVG previews that pass `validate_adventure`. The correction loop (overrides application, `rerun`, playability lint, `estimate`) arrives in phase 3.

The library entry points are `convert()` (the full chain, with per-stage progress events) and `assemble()` (pure re-assembly from cached stage outputs); the `osrforge` console script wraps them:

```sh
osrforge convert my-module.pdf            # full pipeline into ./my-module.forge
osrforge assemble --workdir my-module.forge   # stage caches → artifacts, pure
osrforge preview --workdir my-module.forge    # regenerate the SVG maps only
```

Recording sessions and live verification runs are driven via `tools/extract/run_extraction.py` (see [tools/extract/README.md](tools/extract/README.md)).

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
| `content_batch_pages` | 8 | Content-pass batch size in pages (floor 2) |
| `survey_max_pages` | 150 | Single-request survey guard; larger sources raise `ExtractionError` until survey chunking lands (phase 4) |
| `monster_fuzzy_threshold` | 0.85 | Monster resolution's fuzzy-tier auto-accept floor, pinned against measured catalog pairs |
| `monster_llm_top_k` | 8 | Candidate templates offered per name in the monster-resolution LLM tier |
| `unresolved_fallback` | `best-effort` | Where resolution or parsing came up empty: flagged level-band monster stand-ins and unguarded-treasure rolls (`best-effort`), or leave the gap (`omit`) |

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
