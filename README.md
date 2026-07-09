# osr-forge

Convert tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents: an LLM-assisted extraction pipeline, deterministic map-geometry synthesis, a human correction loop built on an overrides file, and validation against the real osrlib models.

Standalone package + CLI. Consumers need only its artifacts — `adventure.json`, `report.json`, `overrides.yaml`, SVG map previews — or its CLI, regardless of their own tech stack.

**Status:** phase 0 (skeleton and ground truth). See [the specification](docs/spec.md) and [the phase 0 plan](docs/phase-0-plan.md). The pipeline's extraction stages arrive in phase 1; today the package ships the contract types, deterministic preprocessing, and the provider seam. The Foundry capability spike (`tools/spike/`) has not yet run — it needs live Azure credentials — so `docs/foundry-capabilities.md` and the spike-module fixtures don't exist yet.

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

Tests use no network — model interactions replay from recorded fixtures. The only live-network activity in the repo is the manual Foundry capability spike (`tools/spike/`).

## Provider configuration

The Azure AI Foundry adapter reads its connection from `OSRFORGE_FOUNDRY_*` environment variables (an osr-forge-specific prefix, deliberately not `AZURE_OPENAI_*`, to avoid colliding with other tools' conventions):

| Variable | Azure meaning | Required |
| --- | --- | --- |
| `OSRFORGE_FOUNDRY_ENDPOINT` | The Azure OpenAI resource endpoint (`https://<resource>.openai.azure.com`) | yes |
| `OSRFORGE_FOUNDRY_DEPLOYMENT` | The model deployment name | yes |
| `OSRFORGE_FOUNDRY_API_KEY` | The API key. Omit to use Entra ID via `DefaultAzureCredential`, which needs the `osr-forge[entra]` extra | no |

## Licensing

Package code is MIT (see [LICENSE](LICENSE)). osr-forge ships no game content: osrlib's OGL data stays in osrlib, and test assets live under `tests/assets/` outside the built distribution, each with its provenance and license documented in [tests/assets/README.md](tests/assets/README.md).
