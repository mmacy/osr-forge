# Architecture

How the package is organized, which module owns which pipeline stage, and the
rules that keep the pieces decoupled. Terms of art link into the
[glossary](../reference/glossary.md).

## The pipeline, module by module

The chain is `preprocess → survey → content → monsters → assemble`, driven by
[`convert`][osrforge.convert.convert] and resumable per stage by
[`rerun`][osrforge.convert.rerun]. Each extraction stage makes its model
calls once and writes a [stage cache](../reference/glossary.md#stage-cache);
everything after the caches is deterministic.

| Stage | Module | Reads | Writes |
| --- | --- | --- | --- |
| `preprocess` | [`osrforge.preprocess`][] | the source PDF | `source.pdf`, `pages/*.png`, `pages/*.txt`, a fresh `run.json` |
| `survey` | [`osrforge.survey`][] | page renders + text layers | `stages/survey.json` |
| `content` | [`osrforge.content`][] | the survey cache, pages | `stages/areas.<dungeon>.<level>.json` |
| `monsters` | [`osrforge.monsters`][] | the survey and content caches, pages | `stages/monsters.json`, `stages/statblocks.json` |
| `geometry` | [`osrforge.geometry`][] | the survey and content caches | nothing — recomputed inside every assembly |
| `assemble` | [`osrforge.assemble`][] | every cache + `overrides.yaml` | `adventure.json`, `report.json`, `previews/*.svg` |

Around the chain:

- [`osrforge.workdir`][] owns every path in the
  [workdir](../reference/glossary.md#workdir) and the stage-status tracking —
  stages never build paths themselves.
- [`osrforge.check`][] is the post-assembly playability lint and smoke delve;
  it merges [findings](../reference/glossary.md#finding) into `report.json`.
- [`osrforge.overrides`][] applies `overrides.yaml` during assembly;
  [`osrforge.previews`][] renders the SVG maps;
  [`osrforge.estimate`][] prices a conversion from preprocessing alone.
- [`osrforge.evals`][] is the deterministic scorer for the eval harness —
  package code, but driven by on-demand tooling, never by the pipeline.
- [`osrforge.cli`][] wraps the library API one command per function.

## The workdir is the data bus

Stages communicate only through files in the workdir — there is no in-memory
handoff between stages. That is what makes `rerun` possible (any stage can be
re-run from its upstream files), keeps host-app integration
language-agnostic (the artifacts are JSON, YAML, and SVG), and makes every
conversion debuggable after the fact by archiving one directory. The layout
is documented in [the workdir and artifacts](../reference/workdir-artifacts.md).

## Layering rules

- **Wire formats live in `contracts/`.** Anything serialized between stages
  or to a consumer — `run.json`
  ([`osrforge.contracts.run`][]), the stage caches
  ([`osrforge.contracts.stages`][]), `report.json`
  ([`osrforge.contracts.report`][]), and `overrides.yaml`
  ([`osrforge.contracts.overrides`][]) — is a frozen pydantic model there,
  never a shape defined inside a stage module.
- **Extraction stages never import each other.** `survey`, `content`, and
  `monsters` share data through the caches and shared code through
  `contracts/`, [`osrforge.pages`][], and [`osrforge.workdir`][] only.
- **Deterministic downstream code may reuse stage helpers.** `geometry` and
  `assemble` import pure functions from the stage modules (for example
  [`encounter_names`][osrforge.monsters.encounter_names], *the* resolution
  population rule) precisely so producer and consumer can never disagree
  about a derivation.
- **Vendor SDKs stay in adapters.** Pipeline code sees only the
  [`ModelProvider`][osrforge.providers.base.ModelProvider] protocol; the
  Azure AI Foundry specifics live in
  [`osrforge.providers.foundry`][] and nowhere else. Tests run on
  [`FixtureProvider`][osrforge.providers.fixtures.FixtureProvider] —
  see [testing](testing.md).

## Where model spend happens — and where it can't

Only `survey`, `content`, and `monsters` call the provider, and each guards
its spend: the survey chunks only when the module exceeds one request's page
budget, the content pass batches pages, and monster resolution runs its
[deterministic tiers](../reference/glossary.md#resolution-tiers) first,
calling the model only for names the tiers missed. `preprocess`, `geometry`,
`assemble`, `check`, and `estimate` never touch a provider —
[assembly purity](../reference/glossary.md#assembly-purity) is a structural
property, not a convention.

## Determinism

Everything after the stage caches is pinned: sorted JSON keys, pinned
iteration and placement orders in geometry synthesis, and version stamps kept
out of the caches. The payoff is
[byte-stability](../reference/glossary.md#byte-stability) — the pipeline
tests compare full-chain output byte-for-byte against committed
[goldens](../reference/glossary.md#goldens), and a correction applied through
`overrides.yaml` re-assembles instantly and reproducibly.

## The public surface

The API reference renders exactly each module's `__all__` — the importable
surface and the documented surface are the same list, one home per symbol.
[`osrforge`][] (the package façade) re-exports the names the library API
promises; everything else is imported from its owning module.
