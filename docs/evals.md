# Evals

Extraction changes are measured, not vibed: a corpus of freely licensed
adventures with hand-checked structural ground truth, scored on four pinned
metric families, with the results on a committed scoreboard. Evals are
on-demand — never per-commit CI — because extraction is nondeterministic run
to run and live runs cost real money; the *scorer* itself
([`osrforge.evals`][osrforge.evals]) is deterministic, fully unit-tested
package code.

## The corpus

A corpus member is a directory under `tools/eval/corpus/` holding exactly two
files: a `manifest.yaml` (title, source URL, the sha256 of the exact PDF the
truth was authored against, page count, and the license-verification record)
and a `truth.yaml` (hand-checked structural ground truth — printed keys,
creature names, and codes; no prose). The corpus ships pointers plus hashes,
never PDFs.

Truth is authored from the printed module, never from pipeline output —
reading `survey.json` before writing a module's truth file would contaminate
the measurement with the thing being measured.

The v1 members: **minimod** (the in-repo CC0 test module — the zero-cost smoke
of the whole harness), **JN1 The Chaotic Caves** (CC BY-SA 4.0), and **JN2 The
Monkey Isle** (CC BY-SA 4.0, held out — the first module the extraction
prompts were never developed against, and therefore v1's only honest quality
number; minimod and JN1 tuned every prompt, so they measure regression).

## The metric families

Each is reported per module and as the corpus mean, and each reads the stage
caches — never `adventure.json`, whose best-effort fallbacks exist to mask
extraction gaps:

- **Areas** — recall (matched truth areas over truth areas) plus precision
  (the hallucination guard: matched extracted areas over all extracted).
- **Encounters** — name recall, count accuracy over encounters where the
  module states a fixed count, and resolution accuracy against the osrlib
  catalog id the name should resolve to (truth entries with no SRD template
  are excluded and tallied as `non_srd`).
- **Connections** — F1 over undirected same-level edges between matched
  areas, within the truth file's asserted universe.
- **Treasure** — presence agreement (did extraction *see* the treasure the
  module states, unparsed strings included) and letter accuracy where the
  module states treasure-type codes.

## Running the harness

```sh
uv run tools/eval/run_eval.py convert <module-id> <pdf-path> --workdir DIR   # live: verify sha256, then convert
uv run tools/eval/run_eval.py score <module-id> --workdir DIR [--update-scoreboard]
uv run tools/eval/run_eval.py report
```

The sha256 gate runs before any model spend: a PDF that isn't the exact
release the truth was authored against is refused.

## The scoreboard and the regression rule

`tools/eval/scoreboard.json` records, per module, the run that produced it
(date, model id, package version, tokens, USD) and the metrics block. Because
extraction is nondeterministic, the baseline sweep runs twice and the
regression band is the observed per-metric spread, floored at 0.02 absolute.

The regression rule: any PR that edits extraction prompts or schemas, the
monster alias table, resolution logic, or the model deployment re-runs the
sweep and commits the updated scoreboard in the same PR. A metric dropping by
more than the band requires an explicit justification in the PR description;
silence is a blocked merge.

The full corpus rules, authoring conventions, and per-module license records
live in
[`tools/eval/README.md`](https://github.com/mmacy/osr-forge/blob/main/tools/eval/README.md).
