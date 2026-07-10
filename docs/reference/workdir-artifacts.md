# The workdir and artifacts

`convert` creates one directory per module; every stage reads and writes here
and nowhere else. Host apps archive the whole workdir to get debuggability and
stage-level re-runs for free.

```text
my-module.forge/
├── source.pdf            # the copied source module
├── run.json              # source hash, settings echo, per-stage status + token usage
├── pages/
│   ├── 0001.png          # page renders (default 150 DPI)
│   └── 0001.txt          # page text layers (empty for scanned modules)
├── stages/               # cached extraction-stage outputs
│   ├── survey.json
│   ├── areas.<dungeon>.<level>.json
│   └── monsters.json
├── overrides.yaml        # the human correction channel
├── previews/
│   └── <dungeon>.<level>.svg
├── report.json           # the extraction report
└── adventure.json        # the stamped osrlib document
```

There is no geometry cache: geometry is deterministic and recomputed inside
every assembly.

## `adventure.json`

The product — a stamped osrlib document (`kind: "adventure"`, via osrlib's
`stamp_document`; the payload is `Adventure.model_dump(mode="json")`). Load it
with osrlib's `check_document` + `Adventure.model_validate`. Its envelope is
versioned by osrlib itself, so it carries no osr-forge version fields.

## `report.json`

The extraction report, regenerated on every assembly — the complete input a
review UI needs:

- `module` — title and page count.
- `validation` — the `validate_adventure` outcome; a draft is allowed to be
  invalid, and *publishing* it is the consumer's gate.
- `areas[]` — per keyed area: its `<dungeon>/<level>/<key>` address, source
  pages, self-assessed confidence, [flags](vocabulary.md), and which fields an
  override replaced.
- `monsters` — the resolution summary: resolved count and unresolved names.
- `usage` — total input/output tokens.
- `flags[]` — module-scope conditions with no per-area home (a defaulted
  title, an unnamed town), in the same `<flag>` / `<flag>:<detail>` grammar.
- `findings[]` — the playability lint's structured findings (id, severity,
  location, message). Empty from `assemble()` — stale lint about a changed
  draft is worse than none — and populated by `check()`.

`report.json` and `run.json` each carry `schema_version` (osr-forge's artifact
schema version — additive-only within a version) and `osrforge_version` (the
producing package version). The full models are
[`ExtractionReport`][osrforge.contracts.report.ExtractionReport] and
[`RunMeta`][osrforge.contracts.run.RunMeta].

## `overrides.yaml`

The correction channel — see [the correction loop](../guides/correction-loop.md)
for the workflow. The supported kinds:

- **`monsters:`** — remap an extracted name to a catalog `template_id`. Keys
  match under the monsters stage's own normalization (casefold, whitespace
  collapsed).
- **`areas:`** — per-area field replacement (`name`, `description`,
  `encounters`, `trap`, `treasure`, `features`), area removal (`remove:
  true`), and area adds (a new address carrying `name`, `description`, and
  geometry cells).
- **`geometry:`** — per-level cells, edges, entrance, and transitions.
- **`town:` / `module:`** — metadata fields.

The three application rules: keys match under the stage's normalization; every
entry must take effect or assembly fails with a named error; an area add must
carry `name`, `description`, and cells. Duplicate YAML keys are rejected at
load. The schema is
[`Overrides`][osrforge.contracts.overrides.Overrides].

## `previews/*.svg`

One rendered grid map per dungeon level — synthesized geometry, drawn for
eyeballing against the printed map. Regenerate alone with `osrforge preview`.

## Contract stability

From the first PyPI release, the artifact contracts (`adventure.json`'s
stamped document, `report.json`'s flag and finding vocabularies under
`schema_version`, the `overrides.yaml` schema) are additive-only within a
schema version — external consumers read these files, and growth never breaks
a reader.
