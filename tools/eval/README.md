# The eval harness

The on-demand measurement half of the spec's "extraction changes are measured,
not vibed": a corpus of freely licensed adventures with hand-checked
structural ground truth, scored on the four pinned metric families. Evals are
never per-commit CI — extraction is nondeterministic run to run and live runs
cost real money — but the *scorer* is deterministic package code
(`osrforge.evals`), fully unit-tested, with the JN1 pinned baseline in CI at
zero network.

## The corpus

A corpus member is a directory, `corpus/<module-id>/`, holding exactly two
files:

- `manifest.yaml` — title, `source_url` (the pointer — where a human downloads
  the PDF), `sha256` (of the exact PDF the truth was authored against; the
  harness refuses a mismatched file before any model spend), `pages`, and
  `license` (SPDX id plus a `verified` note recording how and when the phase 0
  license procedure was run). The manifest is the whole redistribution
  surface — no module PDF is ever committed (minimod's CC0 PDF, committed as a
  test asset, is the one exception the license permits).
- `truth.yaml` — hand-checked structural ground truth, deliberately prose-free
  to keep the licensing surface thin: printed keys, creature names, and codes
  only.

The v1 members: `minimod` (CC0, authored in-repo — the zero-cost smoke of the
whole harness), `jn1-chaotic-caves` (CC BY-SA 4.0), and `jn2-monkey-isle`
(CC BY-SA 4.0, the held-out member — the first module the extraction prompts
were never developed against, and therefore v1's only honest quality number).
BF1 Morgansfort, the plan's named third candidate, failed the phase 0 license
verification: its copyright line is "Chris Gonnerman and Contributors", and
credited contributor Nicholas Plant does not appear on the project's
relicensing consent list (checked against the Wayback Machine snapshot of
2025-04-18, the newest available), so the CC BY-SA grant could not be cleanly
relied on for the module's text; JN2, the pinned fallback, passed (sole
copyright holder, same verified pattern as JN1). All v1 members share BFRPG
layout conventions — a standing limitation; corpus diversity is future,
additive growth. Growing the corpus is additive: a new module is a manifest, a
truth file, and a sweep — no code.

Fetching stays manual: manifests carry source URLs, but URLs rot, the sha256
check is the integrity gate, and the harness takes no HTTP dependency.

Text derived from the CC BY-SA modules (printed keys, creature names, codes in
their truth files) is distributed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) with
attribution to J.D. Neal and the Basic Fantasy Project.

## Authoring discipline

Truth is authored from the printed module, never from pipeline output —
reading `survey.json` before writing a module's truth file contaminates the
measurement with the thing being measured. (The phase 3 correction session's
notes are legitimate references for JN1 because that session verified every
claim against the printed pages.) The conventions, applied to all v1 members:

- **Dungeons** are the module's keyed adventuring sites — separate lairs with
  their own maps and entrances are separate dungeons even when they share a
  running key sequence (the survey prompt's own rule). Towns, villages, and
  unmapped wilderness/ocean encounter spots are out of truth scope.
- **Encounter names** are the singular creature name as the area's stat block
  or prose prints it (`6 Orcs` → `orc`, count 6). Rank variants with their own
  stat blocks (`orc chief`, `goblin king`) are recorded under their printed
  variant name; `template` is omitted for them and for creatures with no SRD
  catalog entry — the resolution metric excludes those from its denominator
  and tallies them as `non_srd`. Leveled human NPCs map to the catalog's
  fighter NPCs where the level matches (F1 → `veteran_1`, F2 → `veteran_2`,
  F3 → `veteran_3`); clerics, magic-users, and higher-level fighters have no
  same-creature entry and are `non_srd`.
- **Counts** are recorded when the module states a fixed one; omitted when it
  states none or a variable one ("1d6 orcs", "up to 48 skeletons").
- **Treasure** `present` is true when the printed entry states coins,
  valuables, or magic items in the area — carried by its occupants included;
  rewards promised elsewhere ("will pay 200 gp if returned to town") excluded.
  `letters` only when the module states treasure-type letter codes (no v1
  module does — BFRPG modules itemize).
- **Connections** are assertion-aware: an area's `connections` list — possibly
  empty — asserts its *complete* set of same-level connected printed keys, from
  the module's text plus its printed map; the field is omitted where a shared
  hall or winding cave junction makes the neighbor set genuinely
  judgment-dependent. The connection F1 scores only edges with at least one
  asserted endpoint, so an unasserted area never turns a correct extraction
  into a false positive. Areas connected only through unkeyed passages count
  as connected in consecutive order along the passage.

## Running the harness

Everything runs from the repo root. The convert step needs the same
`OSRFORGE_FOUNDRY_*` environment variables as the extraction runner
(`tools/extract/README.md`); scoring and reporting are offline.

```sh
# 1. Verify the local PDF against the manifest and convert (live network, real spend):
uv run tools/eval/run_eval.py convert jn2-monkey-isle ~/Downloads/JN2-Monkey-Isle-r22.pdf --workdir /tmp/jn2.forge

# 2. Score the workdir's stage caches against the truth (offline):
uv run tools/eval/run_eval.py score jn2-monkey-isle --workdir /tmp/jn2.forge --update-scoreboard

# 3. Render the committed scoreboard (offline):
uv run tools/eval/run_eval.py report
```

`convert` runs the package pipeline with `FoundryProvider` — recording off:
evals produce scores, not fixtures. `score` reads the stage caches, never
`adventure.json`: assembly's best-effort fallbacks exist to mask extraction
gaps in the playable draft, which is exactly what a measurement must not let
them do.

## The scoreboard, the noise band, and the regression rule

`scoreboard.json` (committed, beside this file) records, per module, the run
that produced it (date, model id, package version, tokens, USD at the pinned
pricing constants) and the metrics block. Extraction is nondeterministic run
to run, so the baseline sweep runs **twice**: the scoreboard carries the first
run, the phase 4 plan amendment records both runs side by side, and the
regression band is the observed per-metric spread, floored at 0.02 absolute.

The regression rule (also recorded in `AGENTS.md` and the spec): any PR that
edits extraction prompts or schemas, `MONSTER_ALIASES`, resolution logic, or
the model deployment re-runs the sweep and commits the updated scoreboard in
the same PR — the same edits that strand fixtures re-measure quality: one
workflow, two obligations (the fixture re-record rule lives in
`tools/extract/README.md`). A metric dropping by more than the band requires
an explicit justification in the PR description; silence is a blocked merge.
