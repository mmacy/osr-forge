# Install

osr-forge requires Python ≥ 3.14. Install from PyPI with
[uv](https://docs.astral.sh/uv/) or pip:

```sh
uv pip install osr-forge
# or
pip install osr-forge
```

The package name is `osr-forge`; the import is `osrforge`, and the console
script is `osrforge`.

## The `entra` extra

The Azure AI Foundry adapter authenticates with an API key by default. To use
Entra ID (`DefaultAzureCredential`) instead, install the extra:

```sh
uv pip install 'osr-forge[entra]'
```

See [provider setup](../guides/provider-setup.md) for the environment
variables.

## What comes with it

- The `osrforge` CLI: `convert`, `rerun`, `assemble`, `check`, `preview`, and
  `estimate` — see the [CLI reference](../reference/cli.md).
- The library API: [`convert`][osrforge.convert.convert],
  [`assemble`][osrforge.assemble.assemble], [`check`][osrforge.check.check],
  [`estimate`][osrforge.estimate.estimate], and
  [`ConversionSettings`][osrforge.settings.ConversionSettings], re-exported
  from the `osrforge` package root.
- osrlib itself, as a dependency — converted output validates against the real
  models, never a copy.

osr-forge ships no game content: osrlib's SRD data stays in osrlib, and your
module PDFs never leave your machine except as requests to the model provider
you configure.
