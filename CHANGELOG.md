# Changelog

All notable changes to osr-forge are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Custom monster emission (phase 7): a name the resolution tiers leave
  unresolved now gets the module's *own* creature instead of a flagged
  stand-in. The monsters stage's new stat-block pass (gated by the
  `custom_monsters` knob, default `emit`) transcribes each unresolved name's
  printed stat block — over its encounter pages plus deterministic
  text-layer hits, text and images — into the new `stages/statblocks.json`
  cache; assembly deterministically maps usable blocks (an AC plus an HD
  line or class-level notation) into `MonsterTemplate`s under a pinned
  per-format policy, flags every derived field `monster_custom` with the
  full record in the report's new `monsters.custom` section, and bundles
  referenced templates into `Adventure.monsters` (the osrlib 1.2 seam), so
  emitted drafts validate, spawn, and play unchanged.
- The `monster_templates:` override kind: patch fields of an extracted
  name's raw stat block pre-mapping or supply a complete one; an entry on a
  resolved name forces emission — the remedy for a flagless wrong LLM pick.
- The eval custom pair: truth encounters may assert `custom: true`
  (template omitted), scored against the stat-block cache under assembly's
  own usability predicate — `custom_denominator`/`custom_matched`/
  `custom_accuracy` join the encounters family, and JN1/JN2 truths assert
  emission on every template-omitted encounter with a printed stat line.

- Playable structure (phase 6): connections extract their stated mechanism
  (door, secret door, stairs, trapdoor, chute, with stuck/locked conditions)
  and level-shaped targets; geometry synthesizes door and secret-door edges
  on the stating room's wall and level transitions from keyed and
  level-targeted vertical links (opposite-sense links pair into one
  stairway, leftovers land on the target level's first keyed area, trapdoors
  and chutes stay one-way), with every guessed landing flagged by the new
  `transition_guessed` report flag.
- Survey metadata: the module's own description and the town's stated
  services extract into `Adventure.description` and `TownSpec.services`;
  the survey prompt gains the phantom-dungeon rule (a dungeon exists only
  where the module prints a keyed area list) and a counting-anchored
  multi-lair rule.
- The treasure grammar parses comma-grouped numbers (`1,000 cp`) and the
  two quantified-`each` shapes (`3 gems worth 50 gp each`, `3 gems each
  worth 50 gp`); `per`-anything, unquantified `each`, and dice quantities
  stay unparsed.

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

- The monster-resolution LLM prompt is null-hardened: with emission behind
  it, "none of these" now yields the module's own creature, so the prompt
  prefers null on doubt.
- `monster_unresolved` now marks only names with no usable printed block,
  and its generated badge description documents the best-effort stand-in
  detail form (`name → stand-in`).
- The eval scorer matches encounter names under a minimal morphological
  fold — truth's singular authoring convention meets extraction's printed
  plural (`kobold` matches `kobolds`, `lizard man` matches `lizard men`);
  token subsets and renames never match. A fold-matched encounter's count
  compares against the matched group's summed fixed counts, and its
  resolution matches only when every matched extracted name resolved to
  the asserted template. The committed scoreboard and noise band re-score
  the phase 6 sweep pair offline under the fold.
- The eval scorer aligns truth levels to extracted levels by maximal
  area-key overlap, many-to-one from the truth side (the B4 fix: printed
  tiers grouped into coarser extracted levels now score on their areas
  instead of losing them to a level-number mismatch), and skips
  level-targeted connections in the connections family.
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
