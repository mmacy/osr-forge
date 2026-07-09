# osr-forge

Convert tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents: an LLM-assisted extraction pipeline, deterministic map-geometry synthesis, a human correction loop built on an overrides file, and validation against the real osrlib models.

Standalone package + CLI. Consumers need only its artifacts — `adventure.json`, `report.json`, `overrides.yaml`, SVG map previews — or its CLI, regardless of their own tech stack.

**Status:** phase 1 (extraction). See [the specification](docs/spec.md), [the phase 0 plan](docs/phase-0-plan.md), and [the phase 1 plan](docs/phase-1-plan.md). The package ships the contract types, deterministic preprocessing, the provider seam, and both model-calling extraction stages — survey and content — with recorded fixtures and committed stage caches from a full 48-page milestone run over a license-verified module. A playable draft (monster resolution, geometry, assembly, and the `osrforge` CLI) arrives in phase 2.

Extraction runs are driven manually today via `tools/extract/run_extraction.py` (see [tools/extract/README.md](tools/extract/README.md)); the library entry points are `preprocess()`, `survey()`, and `content()`, each reading and writing the workdir the spec defines.

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
