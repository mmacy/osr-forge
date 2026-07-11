# The correction loop

Conversion produces a *draft* — every gap and guess called out in
`report.json` — and corrections live in `overrides.yaml`, never in hand-edits
to generated output. Assembly is pure: applying a correction re-runs no model
and costs nothing.

## The loop

1. **Read the report and the previews.** `report.json` carries per-area
   [flags](../reference/vocabulary.md), the monster-resolution summary, and —
   after `check` — the playability findings. The `previews/*.svg` maps are for
   eyeballing synthesized geometry against the printed map.
2. **Edit `overrides.yaml`.** Monster remaps, per-area field replacement, area
   adds and removes, geometry (cells, edges, entrance, transitions), and
   town/module metadata. Every entry carries a `reason` — corrections are
   reviewable decisions.
3. **`osrforge assemble && osrforge check`.** Re-assembly is instant and pure;
   `check` runs osrlib's `validate_adventure` plus the playability lint and
   exits 0 once validation passes and no error-severity finding remains.
4. **Repeat until publishable.**

## The overrides file

Areas are addressed as `<dungeon-id>/<level-number>/<area-key>`; geometry
entries address a level, `<dungeon-id>/<level-number>`:

```yaml
monsters:
  "hobgoblin chieftain":
    template_id: hobgoblin
    reason: No SRD template for the named chieftain; base hobgoblin is closest.

areas:
  barrow/1/7:
    description: |
      Corrected text copied from p. 14 — extraction merged rooms 7 and 8.
    reason: Extraction merged two rooms.
  barrow/1/95:
    remove: true
    reason: A map-only artifact, not a real room.

geometry:
  barrow/1:
    areas:
      "7":
        cells: [[4, 2], [5, 2], [4, 3], [5, 3]]
    edges:
      "5,2:east": { kind: door, door: { stuck: true } }
    reason: Match the printed map; room 7 is 20' x 20' with a stuck east door.

town:
  name: Riverton
  reason: The module names the base town on p. 3.
```

Three application rules complete the contract:

- Monster override keys match extracted names under the same normalization the
  monsters stage uses (casefold, whitespace collapsed) — `"Hobgoblin
  Chieftain"` still hits `"hobgoblin chieftain"`.
- Every override entry must take effect or assembly fails with a named error —
  no silent no-ops in a correction file.
- An area *add* (an entry addressing an area the draft doesn't have) must
  carry `name`, `description`, and a geometry override supplying its cells.

Duplicate YAML mapping keys are rejected at load: in a hand-edited correction
file a repeated key means two contradictory corrections, and one of them
silently losing is the worst outcome.

## Reading `check`'s findings

`check` merges structured findings into `report.json` and prints them:

```text
$ osrforge check --workdir my-module.forge
validation: passed
warning secret_only_access barrow/1/9 every path into this area passes through a secret door
```

The [finding vocabulary](../reference/vocabulary.md) is enumerated and small.
Errors (an unreachable area, an edge key osrlib would ignore, a blocked smoke
delve) break the loop's exit code; warnings don't — some modules intend
secret-only access, and accepting a warning is a decision the report records.

## What the loop never does

- **Hand-edit `adventure.json`, `report.json`, or the previews.** They are
  regenerated on every assembly; corrections there are lost by design.
- **Re-roll the model to fix content.** If extraction genuinely missed
  something, `osrforge rerun survey` (or `content`, or `monsters`) re-runs
  that stage *and everything downstream* — see
  [settings and rerun](settings-and-rerun.md) — but the correction loop
  itself is model-free.
