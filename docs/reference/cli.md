# CLI

The `osrforge` console script wraps the library API one-to-one. Runtime
failures render as a one-line `osrforge: <message>` and exit code 1;
tracebacks are for bugs.

```text
osrforge convert <module.pdf> [--workdir DIR] [--provider foundry] [--set KEY=VALUE]
osrforge rerun <stage> [--workdir DIR] [--provider foundry] [--set KEY=VALUE]
osrforge assemble [--workdir DIR]
osrforge check [--workdir DIR]
osrforge report [--workdir DIR]
osrforge preview [--workdir DIR]
osrforge estimate <module.pdf> [--workdir DIR]
osrforge --version
```

`assemble`, `check`, `report`, `preview`, and `rerun` discover a missing `--workdir`:
the working directory itself when it is a workdir (it contains `run.json`),
else the unique `*.forge` directory within it. Finding several `*.forge`
directories, or none, is a loud error naming what was found — pass
`--workdir`. An explicit `--workdir` always bypasses discovery. `convert` and
`estimate` instead default to `./<pdf-stem>.forge` beside the PDF.

## convert

Runs the full pipeline — `preprocess → survey → content → monsters →
assemble` — into the workdir (default `./<pdf-stem>.forge`), printing each
stage transition with its token usage. A stage failure stops there, keeps
everything upstream, and the error message carries the exact `osrforge rerun`
command that resumes. On first conversion it also writes a commented
`overrides.yaml` template — the correction loop's on-ramp.

`--set KEY=VALUE` is the repeatable settings channel; values parse as YAML.
See [settings and rerun](../guides/settings-and-rerun.md) for the knob table.

## rerun

Re-runs one stage — `preprocess`, `survey`, `content`, `monsters`, or
`assemble` — and everything downstream of it, from cached upstream outputs.
`--set` updates settings knobs first; a knob owned by a stage upstream of the
rerun stage is rejected (the drift guard). `rerun assemble` makes no model
call and needs no provider configuration.

## assemble

The pure step: stage caches + `overrides.yaml` → `adventure.json`,
`report.json`, and the previews. Instant, deterministic, and byte-stable —
running it twice writes identical files.

## check

Loads the assembled adventure exactly as a consumer does, runs osrlib's
`validate_adventure` plus the playability lint (reachability, orphan cells,
secret-only access, transition pairing, and a seeded smoke delve), merges the
findings into `report.json`, and prints them. Exits 1 exactly when validation
failed or any error-severity finding exists — warnings don't break the
`assemble && check` loop. The finding vocabulary is
[enumerated](vocabulary.md).

## report

Summarizes `report.json` without opening it — the review loop's first read.
Prints the validation status (with the error messages when it failed), the
flags grouped by kind with their locations (module-scope entries at location
`module`), the monster-resolution summary with the unresolved names, and the
playability findings by severity, errors first. Presentation only: machine
consumers keep reading the artifact, and the exit code stays 0 — `check` owns
the gate.

## preview

Regenerates `previews/` from the assembled adventure — each level's SVG plus
`index.html`, which pairs every synthesized map with the module's own map-page
renders for side-by-side comparison. Useful after hand-tuning geometry
overrides when you only want to look at maps. The SVGs carry a coordinate
ruler printing the exact 0-based `x, y` values a geometry override's `cells`
and edge keys use.

## estimate

Preprocesses the PDF (the one step with no model call) and prices the
conversion with pinned heuristics: per-stage token predictions and a USD
figure, with each survey window priced at the doubled rate tier when its
estimated input crosses the 272K-token cliff. The workdir it creates is warm
for `rerun survey`, which continues from the rendered pages — a plain
`convert` starts at preprocess by design and re-renders.

## Provider configuration

`convert` and `rerun` build the provider from `OSRFORGE_FOUNDRY_*` environment
variables — see [provider setup](../guides/provider-setup.md).
