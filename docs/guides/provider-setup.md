# Provider setup

The extraction stages' model access goes through the
[`ModelProvider`][osrforge.providers.base.ModelProvider] protocol — one
`generate` method taking a structured-output request. The shipped adapter is
[`FoundryProvider`][osrforge.providers.foundry.FoundryProvider] (Azure AI
Foundry, OpenAI-compatible chat surface); other vendors are drop-ins behind
the same seam. The tracing stage takes two providers of its own, described
under [the tracing stage's providers](#the-tracing-stages-providers).

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
no vendor SDK is imported outside the adapters in `providers/`.

## The tracing stage's providers

The tracing stage reads printed maps with two model services, each behind
its own protocol, and passes them to the stage as
[`TracingProviders`][osrforge.tracing.stage.TracingProviders]. Every other
stage keeps the `ModelProvider` above.

### Claude, the reading provider

[`ClaudeProvider`][osrforge.providers.claude.ClaudeProvider] fills the
[`ReadingProvider`][osrforge.providers.reading.ReadingProvider] role through
Anthropic's Messages API. It reads its settings from the environment:

| Variable | Meaning | Required |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | The API key | yes |
| `OSRFORGE_CLAUDE_LEDGER` | The path of the Claude cost ledger, a JSON file the provider creates entries in | yes |
| `OSRFORGE_CLAUDE_MODEL` | The model id. One model serves every kind of request. Default `claude-sonnet-5-5` | no |
| `OSRFORGE_CLAUDE_LEDGER_STOP` | The spend in dollars past which the provider refuses to send a request. Default: the ledger file's own `stop_usd` | no |

```python
from osrforge.providers.claude import ClaudeProvider, ClaudeSettings

provider = ClaudeProvider(ClaudeSettings.from_env())
```

Before each request the provider reserves the request's worst-case cost on
the ledger and refuses the request when that would pass the stop. After the
answer it settles the reservation at what the answer's usage cost. The
ledger file is locked while it's read and written, so several conversions
can share one ledger.

### Clef, the decision provider

[`ClefProvider`][osrforge.providers.clef.ClefProvider] fills the
[`DecisionProvider`][osrforge.providers.decisions.DecisionProvider] role
through Cloudflare's Workers AI REST endpoint. It reads its settings from
the environment and your wrangler login:

| Variable | Meaning | Required |
| --- | --- | --- |
| `OSRFORGE_CLEF_ACCOUNT_ID` | The Cloudflare account id | yes |
| `OSRFORGE_CLEF_LEDGER` | The path of the Clef cost ledger | yes |
| `OSRFORGE_CLEF_TOKEN` | A Cloudflare API token with Workers AI access. Omit to use the token of your `wrangler login` | no |
| `OSRFORGE_CLEF_LEDGER_STOP` | The spend in dollars past which the provider refuses to send a request. Default: the ledger file's own `stop_usd` | no |

```python
from osrforge.providers.clef import ClefProvider, ClefSettings

provider = ClefProvider(ClefSettings.from_env())
```

The provider never writes the token anywhere: not to a log, a recording, or
an error message. It sends each crop as PNG and rebuilds a request body over
100 KB with the crop as JPEG at quality 90, because Workers AI rejects
bodies far smaller than its documented limit. Each request's recording
records which format it used. The ledger works as Claude's does, with one
entry per run that counts the requests settled against it.

Clef has no version pin, so the model behind a request can change between
runs. Before a sweep, the drift check
([`drift_check`][osrforge.providers.decisions.drift_check]) sends a sample
of recorded requests again with their exact stored bodies and stops the
sweep if a top answer changes or any probability moves by more than 0.03.
