# Evals

Extraction changes are measured, not vibed: a corpus of freely licensed
adventures with verified structural ground truth, scored on four pinned
metric families, with the results on a committed scoreboard. Evals are
on-demand — never per-commit CI — because extraction is nondeterministic run
to run and live runs cost real money; the *scorer* itself
([`osrforge.evals`][osrforge.evals]) is deterministic, fully unit-tested
package code.

## The corpus

A corpus member is a directory under `tools/eval/corpus/` holding two files:
a `manifest.yaml` (title, source URL, the sha256 of the exact PDF the truth
was authored against, page count, the license-verification record, and the
truth-provenance record) and a `truth.yaml` (verified structural ground truth
— printed keys, creature names, and codes; no prose). The corpus ships
pointers plus hashes, never PDFs.

Truth is authored from the printed module under the independence discipline
([`tools/eval/AUTHORING.md`](https://github.com/mmacy/osr-forge/blob/main/tools/eval/AUTHORING.md)),
never from pipeline output — reading `survey.json` before writing a module's
truth file would contaminate the measurement with the thing being measured.
The discipline was never "a human must do it"; it is "the measuring
instrument must be independent of the system under test": agents author,
an adversarial pass verifies, and humans audit. (Each member's manifest
records which verification legs its truth actually received — the committed
v1 truths predate the adversarial pass, and their `truth_provenance` blocks
say so rather than claiming it retroactively.)

The v1 members: **minimod** (the in-repo CC0 test module — the zero-cost smoke
of the whole harness), **JN1 The Chaotic Caves** (CC BY-SA 4.0), and **JN2 The
Monkey Isle** (CC BY-SA 4.0, held out — the first module the extraction
prompts were never developed against, and therefore v1's only honest quality
number; minimod and JN1 tuned every prompt, so they measure regression).

## The metric families

Each is reported per module and as the corpus mean, and each reads the stage
caches — never `adventure.json`, whose best-effort fallbacks exist to mask
extraction gaps:

- **Areas** — recall (matched truth areas over truth areas), precision
  (the hallucination guard: matched extracted areas over all extracted), and
  the dungeon alignment counts (`truth_dungeons` / `extracted_dungeons` /
  `matched_dungeons`) that make a survey mode-flip legible in the record.
  Within an aligned dungeon, truth levels align to extracted levels by
  maximal area-key overlap, many-to-one from the truth side — a module whose
  printed tiers extraction grouped into coarser levels still scores on its
  areas instead of losing them to a level-number mismatch.
- **Encounters** — name recall, count accuracy over encounters where the
  module states a fixed count, and resolution accuracy against the osrlib
  catalog id the name should resolve to (truth entries with no SRD template
  are excluded and tallied as `non_srd`). Names match under a minimal
  morphological fold — truth's singular authoring convention meets
  extraction's printed plural (`kobold` matches `kobolds`, `lizard man`
  matches `lizard men`) — while token subsets and renames never match: a
  `hobgoblin chief` is not a `hobgoblin`, and a renamed creature stays the
  extraction disagreement the metric should report. A fold-matched
  encounter's count compares against the whole matched group's summed fixed
  counts, and its resolution matches only when every matched extracted name
  resolved to the asserted template.
- **Connections** — F1 over undirected same-level edges between matched
  areas, within the truth file's asserted universe.
- **Treasure** — presence agreement (did extraction *see* the treasure the
  module states, unparsed strings included) and letter accuracy where the
  module states treasure-type codes, within the truth file's asserted
  universe: like connections, an area's treasure block is asserted or
  omitted, so partial truth stays honestly denominated.

## Running the harness

```sh
uv run tools/eval/run_eval.py convert <module-id> <pdf-path> --workdir DIR   # live: verify integrity, then convert
uv run tools/eval/run_eval.py score <module-id> --workdir DIR [--update-scoreboard]
uv run tools/eval/run_eval.py report
```

The integrity gate runs before any model spend: a PDF that isn't the exact
file the truth was authored against is refused.

## Private (BYOM) corpora

The same harness measures locally owned, non-redistributable retail modules
— the package's primary use case. A private corpus is any directory with the
same member layout, selected with `--corpus DIR` on every subcommand; its
scoreboard sits beside its members (`<corpus-dir>/scoreboard.json`). Retail
PDF hashes are copy-specific (watermarks), so a private manifest may omit the
sha256 pin — integrity flows through a local `source.sha256` sidecar seeded
the first time the harness sees the module's source, and enforced everywhere
after.

Publishing is the explicit, outward-facing step:

```sh
uv run tools/eval/run_eval.py convert my-module <pdf> --corpus ~/my-corpus --workdir DIR [--set KEY=VALUE ...]
uv run tools/eval/run_eval.py score my-module --corpus ~/my-corpus --workdir DIR --update-scoreboard
uv run tools/eval/run_eval.py publish my-module --corpus ~/my-corpus
uv run tools/eval/run_eval.py report --byom
```

`publish` copies the module's scored entry onto the committed BYOM board
(`tools/eval/byom-scoreboard.json`) — identity metadata, the run block, the
truth-file hash, non-default settings knobs, and the metrics; aggregate
counts and ratios only, never module text — and refuses a module without a
scored entry or truth provenance (the authoring discipline in
[`tools/eval/AUTHORING.md`](https://github.com/mmacy/osr-forge/blob/main/tools/eval/AUTHORING.md);
the human leg of it is the [owner sampling guide](guides/owner-sampling.md)).
The BYOM board is **advisory**: owner-refreshed, `osrforge_version`-stamped,
never merge-gating — it answers "how does osr-forge perform in general,"
while the committed corpus scoreboard remains the reproducible gate.

## The scoreboard and the regression rule

`tools/eval/corpus/scoreboard.json` records, per module, the run that
produced it (date, model id, package version, tokens, USD), any non-default
settings knobs, and the metrics block. Because extraction is
nondeterministic, the baseline sweep runs twice and the regression band is
the observed per-metric spread, floored at 0.02 absolute — the current band
lives as a table in
[`tools/eval/README.md`](https://github.com/mmacy/osr-forge/blob/main/tools/eval/README.md).

The regression rule: any PR that edits extraction prompts or schemas, the
monster alias table, resolution logic, or the model deployment re-runs the
sweep and commits the updated scoreboard in the same PR. A metric dropping by
more than the band requires an explicit justification in the PR description;
silence is a blocked merge.

The full corpus rules, authoring conventions, and per-module license records
live in
[`tools/eval/README.md`](https://github.com/mmacy/osr-forge/blob/main/tools/eval/README.md).
