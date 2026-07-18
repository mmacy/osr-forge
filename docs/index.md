# osr-forge

Convert tabletop adventure module PDFs into playable
[osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents: an
LLM-assisted extraction pipeline, deterministic map-geometry synthesis, a human
correction loop built on an overrides file, and validation against the real
osrlib models.

Input: a B/X-compatible module PDF — digital or scanned. Output: a draft
adventure that loads through osrlib's own validation, an extraction report
describing what the pipeline was and wasn't sure about, and an overrides file
through which humans correct the draft reproducibly.

## The four artifacts

Everything a consumer touches is one of four artifacts, produced into a
per-module working directory:

| Artifact | Role |
| --- | --- |
| `adventure.json` | The product: a stamped osrlib document. Load with `check_document` + `Adventure.model_validate`. |
| `report.json` | The extraction report: per-area confidence, source pages, flags, monster-resolution summary, lint findings. Drives review UIs. |
| `overrides.yaml` | The human correction channel. Every entry carries a `reason`; applied during assembly; version-controllable. |
| `previews/*.svg` | One rendered grid map per dungeon level, for eyeballing geometry against the printed map. |

osr-forge is front-end-agnostic by contract, not by implementation: it is
Python (it must import osrlib to validate natively), but consumers only need
these artifacts — JSON and YAML files any stack can read — or the CLI.

## The core guarantee

**Assembly is pure.** `adventure.json`, `report.json`, and the previews are a
deterministic function of the cached stage outputs plus `overrides.yaml`. LLM
calls happen only in the extraction stages, whose outputs are cached on disk;
correcting a draft never re-rolls the model, and re-running assembly after an
overrides edit is instant and reproducible.

## Where to go next

- [Install](getting-started/install.md) and run
  [a first conversion](getting-started/first-conversion.md).
- Learn [the correction loop](guides/correction-loop.md) — the workflow that
  takes a draft to publishable.
- Wire up [the Foundry provider](guides/provider-setup.md).
- Read the [CLI](reference/cli.md) and
  [artifact](reference/workdir-artifacts.md) references, or the generated
  [API reference](reference/api/index.md).
- Contributing? [Get set up](contributing/index.md), read the
  [architecture](contributing/architecture.md), and meet the
  [testing model](contributing/testing.md) — the
  [glossary](reference/glossary.md) defines the terms of art along the way.
- See how extraction quality is [measured, not vibed](evals.md).
