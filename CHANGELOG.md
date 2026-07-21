# Changelog

All notable changes to osr-forge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-20

The first release: the complete pipeline from module PDF to a draft osrlib
adventure, the reproducible correction loop, measured extraction quality, and
a documentation site. From this release the artifact contracts
(`adventure.json`'s stamped document, `report.json`'s flag and finding
vocabularies under `schema_version`, the `overrides.yaml` schema) are
additive-only within a schema version.

### The pipeline

- `preprocess → survey → content → monsters → assemble`: page rendering and
  text-layer extraction (pypdfium2); a whole-module survey pass (chunked into
  page windows past `survey_max_pages`, with a deterministic raw-level merge)
  that also lifts the module's own description and the town's stated services
  into `Adventure.description` and `TownSpec.services`, under a phantom-dungeon
  rule (a dungeon exists only where the module prints a keyed area list) and a
  counting-anchored multi-lair rule; per-level batched content extraction whose
  connections carry their stated mechanism (door, secret door, stairs,
  trapdoor, chute, with stuck/locked conditions) and level-shaped targets, and
  whose treasure grammar parses comma-grouped numbers (`1,000 cp`) and the two
  quantified-`each` shapes; and pure assembly into `adventure.json`,
  `report.json`, and SVG level previews.
- Four-tier monster resolution against the osrlib SRD catalog, with custom
  emission behind it: a name the tiers leave unresolved gets the module's *own*
  creature instead of a flagged stand-in. A stat-block pass (the
  `custom_monsters` knob, default `emit`) transcribes each unresolved name's
  printed block into `stages/statblocks.json`; assembly maps usable blocks (an
  AC plus an HD line or class-level notation) into `MonsterTemplate`s under a
  pinned per-format policy, flags every derived field `monster_custom` with the
  full record in the report's `monsters.custom` section, and bundles referenced
  templates into `Adventure.monsters` (the osrlib 1.2 seam), so emitted drafts
  validate, spawn, and play. The resolution LLM prompt is null-hardened — "none
  of these" now yields the module's own creature, so it prefers null on doubt,
  and `monster_unresolved` marks only names with no usable printed block.
- Deterministic grid-geometry synthesis: door and secret-door edges on the
  starting room's wall, and level transitions from keyed and level-targeted
  vertical links (opposite-sense links pair into one stairway, leftovers land
  on the target level's first keyed area, trapdoors and chutes stay one-way),
  with every guessed landing flagged by the `transition_guessed` report flag.
- Providers behind one protocol: `FoundryProvider` (Azure AI Foundry, key or
  Entra ID auth) and `FixtureProvider` (recorded replays — how the whole test
  suite runs with zero network).
- `estimate`: preprocess-only cost prediction with per-window survey pricing
  and the 272K-token pricing-cliff check applied per window.

### The correction loop

- `overrides.yaml`: monster remaps, per-area field replacement, area adds and
  removes, geometry, town/module metadata, and — via the `monster_templates`
  kind — patches to an extracted name's raw stat block or a complete
  replacement (an entry on a resolved name forces emission, the remedy for a
  flagless wrong LLM pick). Every entry carries a reason, every entry must take
  effect, and duplicate keys are rejected.
- `rerun <stage>`: resume any stage and everything downstream from cached
  outputs, with the `--set` drift guard rejecting knobs owned by upstream
  stages.
- `check`: osrlib content validation plus the playability lint — reachability,
  orphan cells, secret-only access, transition pairing, edge-key validity, and
  a seeded smoke delve through the real osrlib engine.

### Evals

- The scorer (`osrforge.evals`): deterministic alignment and metric families
  over the stage caches — areas, encounters (with `custom_*` accuracy for
  emitted templates), connections, and treasure — with the JN1 pinned baseline
  in CI. Encounter names match under a minimal morphological fold (`kobold`
  matches `kobolds`, `lizard man` matches `lizard men`; token subsets and
  renames never match), truth levels align to extracted levels by maximal
  area-key overlap (many-to-one from the truth side), `treasure` is
  assertion-aware so partial truth stays honestly denominated, and each entry
  echoes non-default settings knobs as `settings_overrides`.
- The committed corpus (`tools/eval/corpus/`): minimod, JN1 The Chaotic Caves,
  and the held-out JN2 The Monkey Isle — manifests with sha256 integrity gates
  and hand-checked structural truth files, scored onto
  `tools/eval/corpus/scoreboard.json`.
- Private (BYOM) corpora: every `run_eval.py` subcommand takes `--corpus DIR`,
  manifests may omit `sha256`/`license` for non-redistributable retail modules
  (integrity flows through a local `source.sha256` sidecar seeded on first
  sight and enforced at every convert and score), and an explicit `publish`
  step copies a private corpus's aggregate-only scores — never module text —
  onto the committed BYOM scoreboard, advisory and never merge-gating. The
  truth-authoring runbook (`tools/eval/AUTHORING.md`) carries the independence
  discipline, the adversarial verification pass, and the owner-sampling bar
  behind every published truth file.

### Docs and packaging

- The documentation site at <https://mmacy.github.io/osr-forge/>: guides, CLI
  and artifact references, a generated API reference, a glossary, and a
  Contributing section (setup and gates, the stage-to-module architecture map
  and layering rules, and the testing model — fixtures, request fingerprints,
  goldens). The first-touch API surfaces (`convert`, `rerun`, `estimate`,
  `check`, `load_overrides`, `score_workdir`, the provider protocol) carry
  runnable examples, and the contract and metric models document every field.
- The tag-driven release pipeline: dist audit (no game content, no test or
  tool files in the artifacts), fresh-venv install smoke on both OSes, and
  PyPI trusted publishing.

[Unreleased]: https://github.com/mmacy/osr-forge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mmacy/osr-forge/releases/tag/v0.1.0
