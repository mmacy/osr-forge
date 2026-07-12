# Changelog

All notable changes to osr-forge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Private (BYOM) eval corpora: every `tools/eval/run_eval.py` subcommand
  takes `--corpus DIR`, `convert` takes the main CLI's repeatable `--set`,
  manifests may omit `sha256`/`license` for non-redistributable retail
  modules (integrity flows through a local `source.sha256` sidecar seeded on
  first sight and enforced at every convert and score), and manifests gain
  `publisher`/`edition` identity fields and a `truth_provenance` record.
- The BYOM publish path: the explicit `run_eval.py publish` step copies a
  private corpus's scored entry onto the committed, aggregate-only BYOM
  scoreboard (`tools/eval/byom-scoreboard.json`; refused without a scored
  entry or truth provenance), rendered by `report --byom`. Advisory
  standing: never merge-gating.
- The truth-authoring runbook (`tools/eval/AUTHORING.md`): the independence
  discipline, the adversarial verification pass, and the owner-sampling bar
  behind every published truth file.

### Changed

- The eval scorer: `treasure` in truth files is assertion-aware (an omitted
  block keeps the area out of both treasure denominators — partial truth
  stays honestly denominated), and the areas family gains
  `truth_dungeons`/`extracted_dungeons`/`matched_dungeons`, making a survey
  mode-flip legible in every scoreboard entry. Scoreboard entries also echo
  non-default settings knobs as `settings_overrides`.
- The committed corpus scoreboard moved to `tools/eval/corpus/scoreboard.json`
  (every corpus's scoreboard now lives at `<corpus-dir>/scoreboard.json`).

## [0.1.0] - 2026-07-10

The first release: the complete pipeline from module PDF to a draft osrlib
adventure, the reproducible correction loop, measured extraction quality, and
a documentation site. From this release the artifact contracts
(`adventure.json`'s stamped document, `report.json`'s flag and finding
vocabularies under `schema_version`, the `overrides.yaml` schema) are
additive-only within a schema version.

### The pipeline

- `preprocess → survey → content → monsters → assemble`: page rendering and
  text-layer extraction (pypdfium2), a whole-module survey pass (chunked into
  page windows past `survey_max_pages`, with a deterministic raw-level merge),
  per-level batched content extraction, four-tier monster resolution against
  the osrlib SRD catalog, deterministic grid-geometry synthesis, and pure
  assembly into `adventure.json`, `report.json`, and SVG level previews.
- Providers behind one protocol: `FoundryProvider` (Azure AI Foundry, key or
  Entra ID auth) and `FixtureProvider` (recorded replays — how the whole test
  suite runs with zero network).
- `estimate`: preprocess-only cost prediction with per-window survey pricing
  and the 272K-token pricing-cliff check applied per window.

### The correction loop

- `overrides.yaml`: monster remaps, per-area field replacement, area adds and
  removes, geometry, and town/module metadata — every entry carries a reason,
  every entry must take effect, and duplicate keys are rejected.
- `rerun <stage>`: resume any stage and everything downstream from cached
  outputs, with the `--set` drift guard rejecting knobs owned by upstream
  stages.
- `check`: osrlib content validation plus the playability lint — reachability,
  orphan cells, secret-only access, transition pairing, edge-key validity, and
  a seeded smoke delve through the real osrlib engine.

### Evals

- The eval corpus (`tools/eval/corpus/`): minimod, JN1 The Chaotic Caves, and
  the held-out JN2 The Monkey Isle — manifests with sha256 integrity gates and
  hand-checked structural truth files.
- The scorer (`osrforge.evals`): deterministic alignment and four metric
  families (areas, encounters, connections, treasure) over the stage caches,
  with the JN1 pinned baseline in CI.

### Docs and packaging

- The documentation site at <https://mmacy.github.io/osr-forge/>: guides, CLI
  and artifact references, a generated API reference, and generated badge
  vocabularies.
- The tag-driven release pipeline: dist audit (no game content, no test or
  tool files in the artifacts), fresh-venv install smoke on both OSes, and
  PyPI trusted publishing.

[Unreleased]: https://github.com/mmacy/osr-forge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mmacy/osr-forge/releases/tag/v0.1.0
