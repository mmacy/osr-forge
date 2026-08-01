# Changelog

All notable changes to osr-forge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The map-reading stage (`mapread`, between `monsters` and `assemble`): one
  request per surveyed level over its unblanked printed map pages, proposing
  same-level adjacencies, doors on those pairs, and the map-labeled entrance
  — cached as answered in `stages/mapread.json`, gated by the new
  `map_reading` knob (`read`/`off`), and priced by `estimate`.
- Deterministic reconciliation (`osrforge.reconcile`), shared by geometry
  and the eval scorer: a stated prose fact survives any map conflict,
  flagged; a prose absence fills from the map, flagged as adopted; agreement
  is silent. The entrance selection is shared the same way — a resolvable
  map-proposed entrance beats the positional heuristic.
- The `map_disputed` report flag: every map/prose adoption and disagreement
  (edge disputes on the survey-order-first endpoint, the entrance dispute on
  the selected entrance area) and every dropped proposal (module scope, the
  level address in the detail), each detail naming both readings.

- The stat-block veto: the stat-block pass now also covers LLM- and
  fuzzy-resolved names, and a deterministic comparator flips any non-exact
  pick whose printed Hit Dice count contradicts the picked template to
  unresolved — the vetoed name emits the module's own creature, and the
  discarded pick rides `monsters.json` (`vetoed_template_id`/`veto_detail`)
  and the report's monsters summary (`vetoed`).
- The survey census: a second, reduced request over the survey's page windows
  (sites, levels, and printed key ranges only) compared deterministically
  against the survey; disagreements land on the survey cache and surface as
  module-scope `survey_disputed:<detail>` flags. Flag-only — no re-roll.
- New report flags `resolution_suspect` (a surviving non-exact pick whose
  printed block disagrees on a non-vetoing axis — HD modifier or AC, both
  readings in the detail) and `low_confidence:<value> self-assessed` (an
  area's cached confidence below the pinned 0.6 floor).
- `estimate` prices the census and the widened stat-block pass; the CLI
  table gains a census line.

### Changed

- Geometry now routes every resolved connection the placement tree left
  unrealized — cycle-closing and re-anchored edges alike — so printed loops
  play as loops and stated doors land on a real wall; a genuinely walled-in
  doorless pair flags `connection_ambiguous:edge to <key> not routed`
  instead of staying silent. This reshapes existing synthesized geometry
  even with no map cache present (deliberately; previews and goldens moved).

### The correction loop

- The workdir-taking commands (`assemble`, `check`, `preview`, `rerun`) now
  discover a missing `--workdir`: the working directory when it is itself a
  workdir (contains `run.json`), else the unique `*.forge` directory within
  it. Several `*.forge` directories, or none, is a loud error naming what was
  found; an explicit `--workdir` bypasses discovery entirely.
- A `convert` or `rerun` failure whose workdir records a failed stage now
  appends the exact resume command to the error message:
  `resume with: osrforge rerun <stage> --workdir <dir>`.
- New `osrforge report` command: a read-only summary of `report.json`
  without opening JSON by hand — validation status, flags grouped by kind
  with their locations, the monster-resolution summary with unresolved
  names, and the playability findings by severity. Presentation only; exit
  code stays 0.
- The preview SVGs carry a coordinate ruler printing the exact 0-based cell
  coordinates geometry overrides use, and `previews/index.html` pairs each
  level's synthesized map with the module's own map-page renders for
  side-by-side comparison.

### Evals

- The scorer now reports seven metric families: doors (presence recall and
  precision plus kind and locked accuracy), vertical transitions
  (dungeon-scoped, matched undirected on endpoint pairs), entrance selection,
  and encounter name precision join the original four, backed by
  assertion-aware truth extensions (`doors`, `transitions`, `entrance`, and
  the asserted-empty `encounters: []` convention).
- New `rescore` subcommand on the eval harness: offline scoreboard
  regeneration from retained workdirs — run blocks carried verbatim, metrics
  re-scored against the current truth — for scorer-semantics changes that
  must not re-roll the model.

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
