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
2. **Edit `overrides.yaml`.** Monster remaps and stat-block patches, per-area
   field replacement, area adds and removes, geometry (cells, edges, entrance,
   transitions), and town/module metadata. Every entry carries a `reason` —
   corrections are reviewable decisions.
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

monster_templates:
  "tentacle worm":
    ac: "3 [16]"
    reason: The extracted stat block read AC 8; the page prints 3.

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

When extraction already placed the door — the content pass reads stated
mechanisms, so a "stuck door in the north wall" usually synthesizes on its
own — an `edges` entry is only needed where the door landed on the wrong
edge or didn't land at all (`connection_ambiguous:door to <key> not placed`
names those). An override edge always wins over the synthesized one; the
flag itself persists after the correction — it records what synthesis could
not place, and the report shows both it and the override that answered it.

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

## Correcting monsters

Two override kinds share the monster namespace, and the decision tree is
about what the printed page offers:

- **Wrong SRD pick with a right one available → `monsters:` remap.** The
  classic case: the LLM tier picked a merely similar catalog creature and the
  catalog has the real one.
- **Wrong SRD pick with no right one → `monster_templates:` patch.** An entry
  on a name the tiers *resolved* forces emission of the module's own creature
  from its cached stat block — the remedy for a flagless wrong pick.
- **Bad extracted field → `monster_templates:` patch.** Entries patch the raw
  *printed* block pre-mapping (fix the AC once, not both derived forms);
  absent fields stay extracted, explicit `null` clears one back to unprinted.
- **No block found but one is printed → `monster_templates:` supply.** An
  entry on a name with no cached block forms the candidate block from its own
  fields; it needs at least an AC and an HD line (or class-level notation) —
  the same usability bar assembly holds extracted blocks to.

The same name under both kinds is rejected as contradictory ("use this
catalog id" vs. "use this custom block"), and a `monster_templates:` entry
against a workdir with no stat-block cache — or one written under
`custom_monsters: off` — fails loudly naming the remedy (`rerun monsters`).
Remapping *to* an emitted template id via `monsters:` is legal; the id is in
the draft's catalog union like any other.

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
