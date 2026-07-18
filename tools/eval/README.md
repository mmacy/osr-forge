# The eval harness

The on-demand measurement half of the spec's "extraction changes are measured,
not vibed": a corpus of freely licensed adventures with verified structural
ground truth — authored from the printed module under the independence
discipline ([`AUTHORING.md`](AUTHORING.md)) — scored on the four pinned metric
families. Evals are never per-commit CI — extraction is nondeterministic run
to run and live runs cost real money — but the *scorer* is deterministic
package code (`osrforge.evals`), fully unit-tested, with the JN1 pinned
baseline in CI at zero network.

## The corpus

A corpus member is a directory, `corpus/<module-id>/`, holding two files:

- `manifest.yaml` — title, `source_url` (the pointer — where a human downloads
  the PDF), `sha256` (of the exact PDF the truth was authored against; the
  harness refuses a mismatched file before any model spend), `pages`,
  optional `publisher`/`edition` identity fields, `license` (SPDX id plus a
  `verified` note recording how and when the phase 0 license procedure was
  run), and `truth_provenance` (who or what authored the truth, when, and
  which verification legs ran — see [`AUTHORING.md`](AUTHORING.md)). The
  manifest is the whole redistribution surface — no module PDF is ever
  committed (minimod's CC0 PDF, committed as a test asset, is the one
  exception the license permits).
- `truth.yaml` — verified structural ground truth, deliberately prose-free
  to keep the licensing surface thin: printed keys, creature names, and codes
  only.

Every *committed* member pins `sha256` and carries `license` and
`truth_provenance`, with treasure asserted on every area — a repo test
enforces all of it, so the gating corpus never thins out by accident. The
optional postures in the next section exist for private corpora.

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

## Private (BYOM) corpora

Bring-your-own-module measurement — retail, non-redistributable PDFs, the
package's primary use case — runs through the identical harness: a private
corpus is any directory with the same `<module-id>/manifest.yaml` +
`truth.yaml` layout, selected with `--corpus DIR` on every subcommand. The
differences, all pinned by phase 5:

- **Integrity without a committed pin.** Retail PDF hashes are copy-specific
  (DriveThruRPG-style watermarks stamp every page with the buyer's name), so
  a private manifest may omit `sha256`; integrity then flows through a local
  sidecar, `<module-id>/source.sha256`, seeded the first time the harness
  sees the module's source — at `convert` (from the PDF) or, for a workdir
  converted outside the harness, at first `score` (from `run.json`'s recorded
  hash) — and enforced everywhere after. Cross-copy *identity* is metadata:
  title, publisher, edition, pages.
- **License is optional.** A private corpus is the owner's copy with no
  redistribution surface; the phase 0 verification procedure applies only
  where something derived will be committed, which for BYOM is never.
- **Partial truth is the designed norm.** Complete area-key coverage plus
  honestly asserted `connections`/`treasure` samples — see
  [`AUTHORING.md`](AUTHORING.md).
- **One uniform scoreboard rule.** Every corpus's scoreboard is
  `<corpus-dir>/scoreboard.json` — the committed corpus's is
  `corpus/scoreboard.json`, a private corpus's sits beside its members.
- **Publishing is explicit.** `run_eval.py publish <module-id> --corpus DIR`
  copies the module's scored entry — identity metadata, the run block, the
  `truth.yaml` hash recorded at score time (the yardstick pin), non-default
  settings knobs, and the metrics; aggregate counts and ratios only, never
  module text — onto the committed BYOM board, `byom-scoreboard.json`.
  Publish refuses a module with no scored entry, a truth file edited since
  its entry was scored, a manifest without `truth_provenance`, an id
  colliding with a committed corpus member, or (on update) a title mismatch
  with the entry being replaced. When a module is run more than once, the
  *first* run's scores publish and later runs are recorded in the phase
  amendment — the board holds one current entry per module; history is
  git's job.
- **Advisory standing.** The regression rule binds the committed corpus
  scoreboard only. BYOM entries refresh best-effort by whoever owns the
  module; a stale entry is visible via its `osrforge_version` stamp, never
  blocking. The board answers "how does it perform in general," not "may
  this PR merge."

## Authoring discipline

Truth is authored from the printed module, never from pipeline output —
reading `survey.json` before writing a module's truth file contaminates the
measurement with the thing being measured. (The phase 3 correction session's
notes are legitimate references for JN1 because that session verified every
claim against the printed pages.) The full process — the independence line,
the cross-instrument rule, the adversarial verification pass, and the
owner-sampling bar — is pinned in [`AUTHORING.md`](AUTHORING.md). The
structural conventions, applied to all members:

- **Dungeons** are the module's keyed adventuring sites — separate lairs with
  their own maps and entrances are separate dungeons even when they share a
  running key sequence (the survey prompt's own rule). Towns, villages, and
  unmapped wilderness/ocean encounter spots are out of truth scope.
- **Encounter names** are the singular creature name as the area's stat block
  or prose prints it (`6 Orcs` → `orc`, count 6). The scorer meets extraction's
  printed plural with a minimal morphological fold (`orc` matches `orcs`,
  `lizard man` matches `lizard men`); token subsets and renames never match, so
  rank variants stay distinct creatures. Rank variants with their own
  stat blocks (`orc chief`, `goblin king`) are recorded under their printed
  variant name; `template` is omitted for them and for creatures with no SRD
  catalog entry — the resolution metric excludes those from its denominator
  and tallies them as `non_srd`. Leveled human NPCs map to the catalog's
  fighter NPCs where the level matches (F1 → `veteran_1`, F2 → `veteran_2`,
  F3 → `veteran_3`); clerics, magic-users, and higher-level fighters have no
  same-creature entry and are `non_srd`.
- **Counts** are recorded when the module states a fixed one; omitted when it
  states none or a variable one ("1d6 orcs", "up to 48 skeletons").
- **Treasure** is assertion-aware, exactly like connections: an omitted
  `treasure` block means "not asserted" and keeps the area out of both
  treasure denominators; a present block asserts the area's treasure facts
  completely. `present` is true when the printed entry states coins,
  valuables, or magic items in the area — carried by its occupants included;
  rewards promised elsewhere ("will pay 200 gp if returned to town") excluded.
  `letters` only when the module states treasure-type letter codes (no
  committed member's does — BFRPG modules itemize). Committed members assert
  treasure on every area; partial assertion is a private-corpus posture.
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
(`tools/extract/README.md`); scoring, reporting, and publishing are offline.

```sh
# 1. Verify the local PDF's integrity and convert (live network, real spend):
uv run tools/eval/run_eval.py convert jn2-monkey-isle ~/Downloads/JN2-Monkey-Isle-r22.pdf --workdir /tmp/jn2.forge

# 2. Score the workdir's stage caches against the truth (offline):
uv run tools/eval/run_eval.py score jn2-monkey-isle --workdir /tmp/jn2.forge --update-scoreboard

# 3. Render the committed corpus scoreboard (offline):
uv run tools/eval/run_eval.py report

# A private corpus goes through the identical flow, plus the publish step:
uv run tools/eval/run_eval.py convert my-module ~/modules/my-module.pdf --corpus ~/my-corpus --workdir /tmp/my.forge --set 'blank_page_renders=[21]'
uv run tools/eval/run_eval.py score my-module --corpus ~/my-corpus --workdir /tmp/my.forge --update-scoreboard
uv run tools/eval/run_eval.py publish my-module --corpus ~/my-corpus
uv run tools/eval/run_eval.py report --byom
```

`convert` runs the package pipeline with `FoundryProvider` — recording off:
evals produce scores, not fixtures — and takes the main CLI's repeatable
`--set KEY=VALUE` for runs that need a settings override (the override is
echoed into the scored entry as `settings_overrides`, so a special condition
is visible in the record). `score` reads the stage caches, never
`adventure.json`: assembly's best-effort fallbacks exist to mask extraction
gaps in the playable draft, which is exactly what a measurement must not let
them do.

## The scoreboard, the noise band, and the regression rule

`corpus/scoreboard.json` (committed, inside the corpus directory — every
corpus's scoreboard lives at `<corpus-dir>/scoreboard.json`) records, per
module, the run that produced it (date, model id, package version, tokens,
USD at the pinned pricing constants), any non-default settings knobs, and the
metrics block. Extraction is nondeterministic run to run, so the baseline
sweep runs **twice**: the scoreboard carries the first run, the phase
amendment records both runs side by side, and the regression band is the
observed per-metric spread, floored at 0.02 absolute.

The current noise band — the living table each sweep-pair updates, with
history staying in the phase amendments (this table: the phase 6 double
sweep of 2026-07-17, re-scored offline under the morphological match fold,
superseding the same sweep's exact-match band — the fold collapsed every
between-run spread to the floor, because the old name-recall noise was
mostly the model's singular/plural jitter that exact matching amplified):

| metric | band |
| --- | --- |
| area recall | 0.02 |
| area precision | 0.02 |
| encounter name recall | 0.02 |
| count accuracy | 0.02 |
| resolution accuracy | 0.02 |
| connection F1 | 0.02 |
| treasure presence | 0.02 |

The survey mode-flip phase 4 measured (a JN1 re-roll collapsed ten cave
lairs into one dungeon) did not recur in either phase 6 run — the area
bands above are at the 0.02 floor because both runs surveyed every site
(JN1 14/14/14, JN2 6/6/6 dungeons in both runs) — but it remains the known
failure mode: regression judgment on the area and
name-recall metrics should first check the scoreboard's dungeon counts
(`truth_dungeons` vs `extracted_dungeons`) to see whether the mode flipped,
and a flipped run judges against phase 4's amendment record, not this band.

The regression rule (also recorded in `AGENTS.md` and the spec): any PR that
edits extraction prompts or schemas, `MONSTER_ALIASES`, resolution logic, or
the model deployment re-runs the sweep and commits the updated scoreboard in
the same PR — the same edits that strand fixtures re-measure quality: one
workflow, two obligations (the fixture re-record rule lives in
`tools/extract/README.md`). A PR that changes the *scorer's* matching or
metric semantics carries the offline counterpart: re-score the standing
sweep pair from its retained workdirs, refresh the band, and record the
re-scored pair in the phase amendment — no live spend, same discipline. A
metric dropping by more than the band requires an explicit justification in
the PR description; silence is a blocked merge.
