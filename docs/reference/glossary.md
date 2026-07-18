# Glossary

The project's terms of art, each with a stable anchor. Docstrings and guides
link here; each entry links onward to the page that treats its subject in
depth.

## Workdir { #workdir }

The per-module working directory (`<module>.forge/` by default) that `convert`
creates and every stage reads and writes — source copy, page renders, stage
caches, and the four consumer artifacts. The layout is a contract; see
[the workdir and artifacts](workdir-artifacts.md).

## Pipeline stage { #stage }

One step of `preprocess → survey → content → monsters → assemble`, each
tracked with its own status and token usage in the workdir's `run.json`.
`geometry` is tracked as a stage but has no independent run — it is
recomputed inside every assembly. See
[architecture](../contributing/architecture.md).

## Stage cache { #stage-cache }

An extraction stage's validated output, written under `stages/` in the
workdir (`survey.json`, `areas.<dungeon>.<level>.json`, `monsters.json`,
`statblocks.json`). Caches are what make assembly pure: the model is consulted
once per extraction stage, and everything downstream is a deterministic
function of these files. Their models live in
[`osrforge.contracts.stages`][osrforge.contracts.stages].

## Frozen stage-cache schema { #frozen-schema }

The stage caches' wire formats are pinned (each cache carries a
`schema_version`), and downstream code must tolerate everything a pinned
schema does not forbid — an empty monster name or an empty treasure string is
handled and [flagged](#tolerate-and-flag), never crashed on, because
regenerating a cache costs model spend the consumer already paid.

## Assembly purity { #assembly-purity }

The core guarantee: `adventure.json`, `report.json`, and the previews are a
deterministic function of the cached stage outputs plus `overrides.yaml`.
Correcting a draft never re-rolls the model. Stated for consumers on
[the home page](../index.md); its implementation consequences run through
[architecture](../contributing/architecture.md).

## The correction loop { #correction-loop }

The read-report → edit-`overrides.yaml` → re-assemble → `check` cycle that
takes a draft to publishable, entirely model-free. See
[the correction loop](../guides/correction-loop.md).

## Override { #override }

One entry in `overrides.yaml`, the human correction channel: monster remaps,
stat-block patches, per-area field replacement, geometry, and town/module
metadata, each carrying a `reason`. Every entry must take effect or assembly
fails — no silent no-ops in a correction file. See
[the correction loop](../guides/correction-loop.md).

## Flag { #flag }

One entry in `report.json`'s enumerated per-area vocabulary recording what
extraction or assembly was unsure about (`low_confidence`,
`connection_ambiguous`, `monster_unresolved`, …). Flags describe the built
draft: an override that replaces an input suppresses the flags that input
would have raised, while extraction facts persist. See
[badge vocabularies](vocabulary.md).

## Finding { #finding }

One entry in the playability lint's vocabulary, produced by `check` and
merged into `report.json` — errors break the correction loop's exit code,
warnings record accepted decisions. See
[badge vocabularies](vocabulary.md) and
[the correction loop](../guides/correction-loop.md#reading-checks-findings).

## Tolerate-and-flag { #tolerate-and-flag }

The pipeline's posture toward imperfect input it cannot reject: accept it,
build the best draft the input supports, and record the doubt as a
[flag](#flag) a human can act on. Crashing is reserved for programmer misuse
and corrupted workdirs; module weirdness is data, not an error.

## Best-effort fallback { #best-effort }

The `unresolved_fallback: best-effort` setting's behavior where extraction
came up empty: flagged level-band monster stand-ins and unguarded-treasure
rolls keep the draft playable, while `omit` leaves the gap. See
[settings and rerun](../guides/settings-and-rerun.md).

## Knob { #knob }

One field of [`ConversionSettings`][osrforge.settings.ConversionSettings] —
the deterministic pipeline configuration, echoed into `run.json`. See
[settings and rerun](../guides/settings-and-rerun.md).

## The settings echo { #settings-echo }

The copy of the run's settings stored in `run.json`. It is the single source
of truth every stage reads, so a workdir always knows exactly how its
artifacts were produced. See
[settings and rerun](../guides/settings-and-rerun.md).

## The drift guard { #drift-guard }

The rule that `rerun --set` rejects a settings update whose owning stage is
upstream of the rerun stage — the [settings echo](#settings-echo) is never
allowed to lie about how upstream artifacts were produced. See
[settings and rerun](../guides/settings-and-rerun.md#the-drift-guard).

## Canonical slug { #canonical-slug }

The id and key grammar shared by dungeon ids, area keys, and override
addresses: lowercase `[a-z0-9]+` groups joined by single hyphens. The
alphabet is restrictive on purpose — no `/` (the address grammar uses it), no
`.` (cache filenames and request tags parse unambiguously), no uppercase (a
hand-edited override can't alias `4a` against `4A`). Enforced at the source
by [`CANONICAL_SLUG_PATTERN`][osrforge.contracts.stages.CANONICAL_SLUG_PATTERN].

## Request fingerprint { #request-fingerprint }

A model request's identity hash:
[`ModelRequest.fingerprint`][osrforge.providers.base.ModelRequest.fingerprint],
the sha256 of the request's tag, system text, content parts (images as their
own sha256 + size), and JSON Schema. Fixtures are stored and replayed by
fingerprint, which is why a prompt, schema, or page-render change strands
recorded fixtures. See [testing](../contributing/testing.md).

## Fixture { #fixture }

One recorded model exchange — request digest plus response — written by
[`RecordingProvider`][osrforge.providers.fixtures.RecordingProvider] and
replayed by
[`FixtureProvider`][osrforge.providers.fixtures.FixtureProvider] at zero
network and zero cost. Named `<tag>.<fingerprint[:12]>.json`. See
[testing](../contributing/testing.md).

## Replay-grade vs evidence-grade { #replay-grade }

A *replay-grade* fixture set is closed over committed assets — every byte of
its requests can be rebuilt from the repository, so tests replay it forever.
An *evidence-grade* set embeds content the repository does not commit (page
images of a licensed module); it documents that a run happened and what it
answered, with no replay promise. See [testing](../contributing/testing.md).

## Goldens { #goldens }

Committed expected outputs (stage caches, `adventure.json`, `report.json`,
previews) that pipeline tests compare byte-for-byte. Goldens are regenerated
deliberately by re-running the documented fabrication commands — never
hand-edited. See [testing](../contributing/testing.md).

## Byte-stability { #byte-stability }

The determinism discipline that makes [goldens](#goldens) possible: sorted
JSON keys, pinned iteration and placement orders, and version stamps kept out
of the stage caches, so byte-identical inputs produce byte-identical
artifacts on every platform.

## Resolution tiers { #resolution-tiers }

The monsters stage's four-tier cascade over extracted encounter names, each
tier consulted only when the previous one misses: normalized exact match,
the curated alias table, stdlib fuzzy matching, and one LLM pass over the
remainder. A fully deterministic resolution makes no model call. See
[`osrforge.monsters`][osrforge.monsters].

## The stat-block pass { #stat-block-pass }

The monsters-stage pass (under `custom_monsters: emit`) that transcribes the
printed stat block for each name the [tiers](#resolution-tiers) left
unresolved, caching the raw block for assembly to map into a bundled custom
[`MonsterTemplate`][osrlib.core.monsters.MonsterTemplate]. Transcription is
the pass's whole job — every rules judgment lives in assembly, where it is
deterministic and correctable.

## Truth independence { #truth-independence }

The eval discipline that ground truth is authored from the printed module,
never from pipeline output — the measuring instrument must be independent of
the system under test. See [evals](../evals.md).

## BYOM { #byom }

Bring-your-own-module measurement: private eval corpora over locally owned,
non-redistributable modules, with only aggregate numbers ever committed. See
[evals](../evals.md).
