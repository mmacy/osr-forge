# Provider setup

All model access goes through the
[`ModelProvider`][osrforge.providers.base.ModelProvider] protocol — one
`generate` method taking a structured-output request. The shipped adapter is
[`FoundryProvider`][osrforge.providers.foundry.FoundryProvider] (Azure AI
Foundry, OpenAI-compatible chat surface); other vendors are drop-ins behind
the same seam.

## Environment variables

The CLI builds the Foundry adapter from `OSRFORGE_FOUNDRY_*` environment
variables (an osr-forge-specific prefix, deliberately not `AZURE_OPENAI_*`, to
avoid colliding with other tools' conventions):

| Variable | Azure meaning | Required |
| --- | --- | --- |
| `OSRFORGE_FOUNDRY_ENDPOINT` | The Azure OpenAI resource endpoint (`https://<resource>.openai.azure.com`) | yes |
| `OSRFORGE_FOUNDRY_DEPLOYMENT` | The model deployment name | yes |
| `OSRFORGE_FOUNDRY_API_KEY` | The API key. Omit to use Entra ID via `DefaultAzureCredential`, which needs the `osr-forge[entra]` extra | no |

## Key vs. Entra auth

With `OSRFORGE_FOUNDRY_API_KEY` set, the adapter authenticates with the key.
Without it, the adapter uses Entra ID through `DefaultAzureCredential` — sign
in with `az login` (or any credential source `DefaultAzureCredential`
supports) and install the extra:

```sh
uv pip install 'osr-forge[entra]'
```

## As a library

```python
from osrforge.providers.foundry import FoundryProvider, FoundrySettings

provider = FoundryProvider(FoundrySettings.from_env())
```

[`FoundrySettings`][osrforge.providers.foundry.FoundrySettings] can also be
constructed explicitly when a host app manages configuration itself. The
provider owns retries, rate-limit backoff, and schema enforcement: `generate`
either returns data that validates against the request's JSON Schema or
raises after its retry budget.

## What the provider sees

Preprocessing yields each page's extracted text plus a rendered PNG, and every
extraction request interleaves them per page. The pipeline never depends on
any provider's native PDF ingestion — that is what makes adapters cheap — and
nothing outside `providers/foundry.py` imports a vendor SDK.
