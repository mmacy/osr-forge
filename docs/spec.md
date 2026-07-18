# osr-forge specification

A standalone Python package and CLI that converts tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents. Input: a B/X-compatible module PDF. Output: a draft `adventure.json` validated against the real osrlib models, an extraction report describing what the pipeline was and wasn't sure about, and an overrides file through which humans correct the draft reproducibly.

osr-forge is front-end-agnostic by contract, not by implementation: it's Python (it must import osrlib to validate natively), but its consumers only need its artifacts — JSON and YAML files any stack can read — or its CLI. The first consumer is the osr-web app (`~/repos/osr-web`), which wraps it in a conversion worker and builds a graphical review UI over the same contracts.

## Goals

- Convert a digital or scanned module PDF into a draft osrlib `Adventure` that passes `Adventure.model_validate`, with every gap and guess called out in a machine-readable report.
- Make human correction first-class and reproducible: corrections live in an overrides file, never in hand-edits to generated output, and re-assembly is a pure function of cached pipeline outputs plus overrides.
- Keep the LLM behind a small provider interface. The first adapter targets Azure AI Foundry with `gpt-5.4`; others (Anthropic, local models) are drop-ins later.
- Be usable three ways: as a CLI for humans, as a library for host apps (osr-web's worker), and as an artifact producer for any consumer that just wants the JSON.
- Ship quality evals so extraction changes are measured, not vibed.

## Non-goals

- **No GUI.** Review tooling here is artifacts: SVG map previews, the report, and the overrides file. Consumers build GUIs (osr-web's module workshop) on top of the same contracts.
- **No hosting or service.** It's a package + CLI; queueing, storage, auth, and multi-tenancy belong to host apps.
- **No invented game content.** Monsters resolve against the shipped osrlib SRD catalog first, and for a name the tiers leave unresolved, assembly emits the module's *own* creature — a custom `MonsterTemplate` whose every field either comes off the printed stat block or is derived from printed values by the game's own rules tables, with every derived field flagged. That is extraction, not invention: nothing is ever conjured from vibes, a name with no usable printed block falls back to a flagged, level-appropriate stand-in *from the shipped catalog* (or omission under `unresolved_fallback: omit`), and a human override — a catalog remap or a stat-block patch — is always the last word.
- **Not part of osrlib.** osrlib is pydantic-only and sans-I/O; a converter with LLM and filesystem dependencies stays outside it and depends on it. osrlib's frozen-API posture — like osr-forge's own additive-only artifact fence — is owner-waived while no external consumers exist (declared 2026-07-16; the only consumers today are the owner's own projects); the separation rationale stands on its own.
- **No pixel-perfect map reproduction.** Geometry is synthesized from the extracted room-connection graph and corrected by humans; matching the printed cartography automatically is explicitly out of scope for v1.

## The contract

Everything a consumer touches is one of four artifacts, produced into a per-module working directory:

| Artifact | Format | Role |
| --- | --- | --- |
| `adventure.json` | Stamped osrlib document (`kind: "adventure"` via `osrlib.versioning.stamp_document`; payload is `Adventure.model_dump(mode="json")`) | The product. Load with `check_document` + `Adventure.model_validate`. |
| `report.json` | JSON | Extraction report: per-area confidence, source pages, flags (synthesized geometry, unresolved monsters, ambiguities), monster-resolution summary, token usage. Drives review UIs. |
| `overrides.yaml` | YAML | The human correction channel. Every entry carries a `reason`. Applied during assembly; version-controllable. |
| `previews/*.svg` | SVG | One rendered grid map per dungeon level, for eyeballing geometry against the printed map. |

The core guarantee: **assembly is pure.** `adventure.json`, `report.json`, and the previews are a deterministic function of the cached stage outputs plus `overrides.yaml`. LLM calls happen only in the extraction stages, whose raw outputs are cached on disk; correcting a draft never re-rolls the model, and re-running assembly after an overrides edit is instant and reproducible. (This mirrors osrlib's own `srd_compile` overrides pattern: corrections live beside the pipeline with reasons, never inside generated output.)

## Pipeline

Six stages. Stages 1–3 call the model; 0, 4, and 5 are deterministic code.

1. **Preprocess (0).** Open the PDF with pypdfium2; extract each page's text layer and render each page to a PNG (default 150 DPI, configurable). Scanned modules with no text layer just yield empty `.txt` files — the model reads the images. Enforce limits (page count, file size) and write `run.json` metadata (source hash, page count, settings).
2. **Survey (1).** One structured-output request over the whole document (text + page images), chunked if needed: sources over the survey chunk size are surveyed in page windows of that size, each request carrying its window's pages plus a preamble naming the window, and the windows' answers are merged deterministically before normalization. Identify title, the module's own description (its printed pitch, never invented), hooks, town/base info with stated services, the dungeon(s) and level(s), the keyed-area list per level with page locations, and every monster name that appears. Output is the index that plans the content passes.
3. **Content (2).** Per level, extract keyed areas in batches: description prose, encounters (monster name + count), traps, treasure, features, and connections to other areas ("the corridor continues north to area 7") — each connection carrying its stated mechanism (door, secret door, stairs, trapdoor, chute; stuck/locked conditions) and, when the text states only a level ("stairs descend to the second level"), a level-shaped target instead of a keyed one — each area with `source_pages` and a self-assessed confidence. Requests send only the relevant page images/text plus the survey index. Schemas are small and per-batch — one giant Adventure schema extracts worse and runs into structured-output limits.
4. **Monsters (3).** Resolve extracted names against `osrlib.data.load_monsters()` in tiers: normalized exact id match → curated alias table → stdlib fuzzy match (`difflib`) → one LLM pass that picks from the top-k candidates or answers "none of these" (and prefers null on doubt — an unmatched name keeps its own printed stat block downstream, while a wrong pick silently replaces the module's creature). The stat-block pass then runs over exactly the names still unresolved, gated by the `custom_monsters` knob: one transcription request per name over its planned page set — the union of its encounters' source pages and every page whose text layer contains the name, capped, text plus images (images because tabular stat blocks scramble in extracted text) — cached raw in `stages/statblocks.json` with an explicit absent marker for a name whose pages print nothing. The pass transcribes the printed block system-neutrally; every rules judgment lives in assembly's deterministic mapping. Names with no usable block become report flags, and the draft never carries a dangling id — that would fail `validate_adventure`, and osrlib refuses to open a session over an invalid adventure. What the draft carries for that residue is the `unresolved_fallback` setting's call: `best-effort` (the default) substitutes a deterministic, level-appropriate stand-in from osrlib's shipped dungeon encounter tables, flagged with both names; `omit` leaves the encounter out. Either way, a human override choosing the true substitute is the last word.
5. **Geometry (4).** Deterministic synthesis of grid geometry from the room graph, recomputed inside every assembly rather than cached (which is why the workdir has no geometry cache file): place the entrance area at the origin, walk the connection graph breadth-first, place rooms as rectangular cell clusters (10' per cell, sized from stated dimensions like "30' × 40'" when present, defaults otherwise) in the stated compass direction when known, route 1-cell-wide corridors, emit open `Edge`s — a connection whose stated mechanism is a door or secret door realizes as a `door` edge on the stating room's wall, and one that cannot land there drops the door fact with a `connection_ambiguous` flag — and shift on collision. Vertical links synthesize transitions: keyed targets as stated, level-shaped targets under a total landing policy (opposite-sense links pair into one stairway, leftovers land on the target level's first keyed area, every guessed landing flagged `transition_guessed`); trapdoors and chutes are one-way, stairs reciprocal. Overrides remain the last word on all of it. The output always satisfies osrlib's structural rules — entrance exists, transitions align, cells in bounds — even when it doesn't match the printed map. Every synthesized area is flagged `geometry_synthesized` in the report.
6. **Assemble and check (5).** Apply `overrides.yaml` to the cached stage outputs, map cached stat blocks into custom `MonsterTemplate`s (deterministically, under a pinned per-format policy: a block is usable when it carries an AC plus an HD line or a class-level notation — the refusal ladder — and every derived, defaulted, or discarded-and-rederived field is flagged `monster_custom` and recorded in the report), bundle the templates built encounters actually reference into `Adventure.monsters`, build the `Adventure` via `model_validate`, run `validate_adventure` against the shipped monster/equipment catalogs (which unions bundled templates internally), write the four artifacts. Validation failures land in the report rather than crashing — a draft is allowed to be invalid; *publishing* it somewhere is the consumer's gate.

## The workdir

`convert` creates one directory per module; every stage reads and writes here and nowhere else.

```text
my-module.forge/
├── source.pdf
├── run.json              # source hash, settings, model id, per-stage status + token usage
├── pages/
│   ├── 0001.png
│   └── 0001.txt
├── stages/               # cached raw stage outputs — the LLM's actual answers
│   ├── survey.json
│   ├── areas.<dungeon>.<level>.json
│   ├── monsters.json
│   └── statblocks.json
├── overrides.yaml
├── previews/
│   └── <dungeon>.<level>.svg
├── report.json
└── adventure.json
```

Host apps archive the whole workdir (osr-web stores it in Blob Storage) to get debuggability and stage-level re-runs for free. (There is no geometry cache: geometry is deterministic and recomputed inside every assembly.)

## LLM provider interface

One small protocol; the pipeline knows nothing about vendors:

```python
class ModelProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse:
        """One structured-output completion.

        request: system text, ordered content parts (text and page images),
            and a JSON Schema the response must satisfy.
        response: parsed + schema-validated JSON, plus token usage.
        """
```

Providers own retries, rate-limit backoff, and schema enforcement (native structured outputs where the platform has them; validate-and-retry otherwise).

- **`FoundryProvider`** ships first: Azure AI Foundry, OpenAI-compatible chat surface, model `gpt-5.4` (the deployment available under the user's Visual Studio Enterprise subscription). Auth via API key or Entra ID (`azure-identity` `DefaultAzureCredential`). Confirming gpt-5.4's exact capabilities — JSON-schema output support, image input limits, context length, pricing — is a phase 0 task; the pipeline's chunking and schema sizes are tuned to whatever those turn out to be.
- **`FixtureProvider`** ships alongside it: replays recorded request/response pairs from disk. This is how unit tests, CI, and host-app integration tests run the full pipeline with zero network and zero cost.

Because preprocessing yields text + page images per page, the pipeline never depends on any provider's native PDF ingestion — that's what makes adapters cheap.

## Overrides

YAML, one file per module, human-edited (directly or by a GUI like osr-web's workshop). Every entry has a `reason`. Areas are addressed as `<dungeon-id>/<level-number>/<area-key>`.

```yaml
monsters:
  "hobgoblin chieftain":
    template_id: hobgoblin
    reason: No SRD template for the named chieftain; base hobgoblin is the closest match.

areas:
  barrow/1/7:
    description: |
      Corrected text copied from p. 14 — extraction merged rooms 7 and 8.
    reason: Extraction merged two rooms.

geometry:
  barrow/1:
    areas:
      "7":
        cells: [[4, 2], [5, 2], [4, 3], [5, 3]]
    edges:
      "5,2:east": { kind: door, door: { stuck: true } }
    reason: Match the printed map; room 7 is 20' x 20' with a stuck east door.
```

Supported override kinds in v1: monster remaps, monster template patches (`monster_templates:` — keyed by name like `monsters:`, patching fields of the extracted raw stat block pre-mapping or supplying a complete one; legal on a name the tiers resolved, where it forces emission — the remedy for a flagless wrong LLM pick), per-area field replacement (name, description, encounter, trap, treasure, features), area add/remove, geometry (cells, edges, entrance, transitions), and town/module metadata fields. The same name in both `monsters:` and `monster_templates:` is an error — "use this catalog id" and "use this custom block" are contradictory corrections — and a `monster_templates` entry against a workdir whose stat-block cache is missing or was written under `custom_monsters: off` fails loudly naming the remedy. Overrides apply after stage outputs and before validation, so `check` always evaluates the corrected draft.

Three application rules complete the contract:

- Monster override keys match extracted names under the same normalization the monsters stage uses (casefold, whitespace collapsed) — `"Hobgoblin  Chieftain"` still hits `"hobgoblin chieftain"`.
- Every override entry must take effect or assembly fails with a named error — no silent no-ops in a correction file.
- An area *add* (an entry addressing an area the draft doesn't have) must carry `name`, `description`, and a geometry override supplying its cells.

Duplicate YAML mapping keys are rejected at load: in a hand-edited correction file a repeated key means two contradictory corrections, and one of them silently losing is the worst outcome.

## Extraction report

`report.json` is regenerated on every assembly and is the complete input a review UI needs:

```json
{
  "schema_version": 1,
  "osrforge_version": "0.1.0",
  "module": { "title": "The Example Barrow", "pages": 48 },
  "validation": { "passed": false, "errors": ["..."] },
  "areas": [
    {
      "id": "barrow/1/7",
      "source_pages": [14],
      "confidence": 0.62,
      "flags": ["geometry_synthesized", "monster_unresolved:hobgoblin chieftain"],
      "overridden": ["description"]
    }
  ],
  "monsters": {
    "resolved": 11,
    "unresolved": ["hobgoblin chieftain"],
    "custom": [
      {
        "id": "tentacle_worm",
        "name": "tentacle worm",
        "source_pages": [16],
        "derived": ["ac", "thac0", "treasure"]
      }
    ]
  },
  "usage": { "input_tokens": 412000, "output_tokens": 88000 },
  "flags": ["low_confidence:town name unstated"],
  "findings": [
    {
      "id": "secret_only_access",
      "severity": "warning",
      "location": "barrow/1/9",
      "message": "every path into this area passes through a secret door"
    }
  ]
}
```

Module-scope conditions with no per-area home — a defaulted adventure title or town name — land in the top-level `flags` array, using the same flag grammar as per-area flags.

Flag vocabulary is small and enumerated (geometry synthesized, monster unresolved, monster custom, low confidence, connection ambiguous, transition guessed, treasure unparsed, page unreadable) so UIs can badge reliably. `monster_custom` is the review badge for an emitted template; the monsters summary's `custom` records are the review detail — per bundled template, its id, source name, transcription pages, and every field the mapping derived rather than read off the printed page.

`findings` is empty from `assemble()` (stale lint about a changed draft is worse than none; re-assembly wipes findings by design) and populated by `check()`, which rewrites `report.json` with the findings merged.

`report.json` and `run.json` each carry `schema_version` (osr-forge's own artifact schema version — independent of osrlib's, additive-only within a version) and `osrforge_version` (the producing package version); `adventure.json` needs neither because osrlib's `stamp_document` envelope already versions it.

## Validation and playability checks

`check` runs two tiers against the assembled adventure:

- **Content validation:** `validate_adventure(adventure, load_monsters(), load_equipment())` — osrlib's own fail-fast gate for dangling references and structural problems.
- **Playability lint:** static graph checks the engine doesn't enforce but players feel — every area reachable from the entrance, no orphan cells, level transitions land on valid cells both ways, at least one non-secret path into each keyed area (secret-only access is a warning, not an error, since some modules intend it) — plus a smoke delve: build a seeded `GameSession` with a throwaway party, `EnterDungeon`, and execute a handful of `MoveParty`/`OpenDoor` commands along a computed path to prove the geometry actually plays. Full automated playthroughs are future work.

Findings are structured (id, severity, location, message) and merged into `report.json`. The finding vocabulary, enumerated:

| id | severity | meaning |
| --- | --- | --- |
| `edge_invalid` | error | an edge-map key osrlib would silently ignore — malformed, non-canonical, or referencing an out-of-bounds cell |
| `area_unreachable` | error | no path from any entrance reaches the area (doors of any state count as passable) |
| `orphan_cell` | warning | a non-area cell that renders as corridor but no path reaches it |
| `secret_only_access` | warning | every path into the area passes through a secret door |
| `transition_unpaired` | warning | stairs whose target cell has no transition back (trapdoors and chutes are exempt — one-way by design) |
| `delve_blocked` | error | the smoke delve's static model and the engine disagree on a step |
| `delve_incomplete` | warning | the delve ended early — battle opened or a budget ran out; module difficulty, not a geometry defect |

The smoke delve's shape, pinned: per dungeon, a fresh seeded session on the entrance level, walking the *deterministic* subgraph (open edges plus plain doors — no stuck, locked, or secret) to the farthest reachable cell, evading encounters, and taking one reachable transition. A clean delve emits nothing.

## CLI

```text
osrforge convert <module.pdf> [--workdir DIR] [--provider foundry] [--set KEY=VALUE]
osrforge rerun <stage> [--workdir DIR] [--set KEY=VALUE]  # re-run one stage — and everything downstream of it — from cached upstream outputs
osrforge assemble [--workdir DIR]           # pure: stage outputs + overrides → artifacts
osrforge check [--workdir DIR]              # validate_adventure + playability lint
osrforge preview [--workdir DIR]            # regenerate the SVG maps
osrforge estimate <module.pdf> [--workdir DIR]  # preprocess only; rough token/cost estimate
```

`convert` is `preprocess → survey → content → monsters → assemble` with per-stage status written to `run.json`; a failure stops there, keeps everything upstream, and `rerun` resumes. The intended human loop after conversion is: look at `report.json` and the previews → edit `overrides.yaml` → `osrforge assemble && osrforge check` → repeat until clean. `--set` is the repeatable settings channel (values parse as YAML); `rerun --set` rejects a knob owned by a stage upstream of the rerun stage — the drifted settings echo would otherwise lie about how upstream artifacts were produced.

`estimate` prices a conversion before any model call using page counts, extracted-text volume, and image-token heuristics for the configured provider; hosts surface it as a confirmation ("converting this 48-page module will cost roughly $X").

## Library API

Host apps use the same operations programmatically:

```python
from osrforge import ConversionSettings, assemble, check, convert, estimate

result = convert(pdf_path, workdir, provider=provider, settings=ConversionSettings(), on_progress=cb)
draft = assemble(workdir)   # Adventure + report, pure
findings = check(workdir)
```

`on_progress` receives stage transitions and token usage as they happen (osr-web's worker streams these into its `conversion_runs` table). All functions are synchronous; hosts own their own queueing and threading.

## Configuration

Provider settings come from an explicit settings object (library) or environment/config file (CLI): Foundry endpoint, deployment name, auth mode (key vs. Entra ID). Pipeline knobs with sane defaults: render DPI, max pages, content-pass batch size, the survey chunk size (`survey_max_pages` — a source at or under it surveys in one request; a larger source surveys in page windows of that size), fuzzy-match threshold, top-k for LLM monster matching, blank-page renders (page numbers whose renders are emitted as blank white PNGs, text layer still extracted — the content-safety-filter workaround), custom-monster emission (`custom_monsters` — `emit`, the default, runs the stat-block pass so unresolved names get the module's own creatures; `off` skips the per-unresolved-name model spend and keeps the draft SRD-catalog-pure; owned by the monsters stage, so toggling it re-runs monsters, including its LLM resolution tier), and the unresolved-content fallback policy (`best-effort`, the default — flagged level-band monster stand-ins and unguarded-treasure rolls where resolution or parsing came up empty — vs. `omit`). No global state.

Changing settings on an existing workdir goes through `rerun --set`, guarded by a knob→owning-stage map: a knob owned by a stage upstream of the rerun stage is rejected with the stage to rerun instead.

## Testing and evals

- **Unit tests** cover the deterministic majority — preprocessing, geometry synthesis, override application, assembly, report generation, lint — with no network.
- **Pipeline tests** run end to end against `FixtureProvider` recordings, asserting byte-stable artifacts (assembly purity is itself a test).
- **Evals** are a separate, on-demand harness (not per-commit CI): a corpus of freely licensed adventures (`tools/eval/corpus/`) with verified structural ground truth, authored from the printed module under the independence discipline (`tools/eval/AUTHORING.md`) — never from pipeline output. The scorer (`osrforge.evals`) reads the stage caches and reports four metric families: areas (recall + precision, plus dungeon alignment counts), encounters (name recall, count accuracy, resolution accuracy, and custom-emission accuracy — a truth encounter with `template` asserts the SRD resolution; one with `custom: true` instead asserts *this creature should emit* and matches when the stat-block cache carries a usable block under assembly's own refusal-ladder predicate; omitted-without stays `non_srd`), connections (F1 over undirected same-level edges within the truth's asserted universe), and treasure (presence agreement and letter accuracy, within the truth's asserted universe). Results live on the committed scoreboard, `tools/eval/corpus/scoreboard.json`. Extraction is nondeterministic run to run, so the baseline sweep runs twice and the regression band is the observed per-metric spread floored at 0.02 absolute; any PR that edits extraction prompts or schemas, the alias table, resolution logic, or the model deployment re-runs the sweep and commits the updated scoreboard in the same PR, a PR that changes the scorer's matching or metric semantics re-scores the standing sweep pair offline and refreshes the band the same way, and a metric dropping by more than the band requires explicit justification in the PR description. The corpus ships pointers + hashes, not PDFs. Private (BYOM) corpora over locally owned, non-redistributable modules side-load through the same harness and scorer (`--corpus DIR`, identical layout, integrity via a local hash sidecar when the manifest cannot pin one). The committed BYOM scoreboard (`tools/eval/byom-scoreboard.json`, fed by an explicit `publish` step) is advisory — owner-refreshed, `osrforge_version`-stamped — beside the gating corpus scoreboard.
- **Compatibility:** golden `adventure.json` fixtures are loaded against the pinned osrlib on every CI run, so an osrlib upgrade that changes document semantics fails loudly here first.

## Tooling and packaging

- Python ≥ 3.14 (osrlib's floor), managed with `uv`; `ruff` format/lint, `pyright`, `pytest`.
- Dependencies: `osrlib` (compatible-range pin, `>=1.2,<2`), `pypdfium2` (rendering + text extraction; permissive license, unlike PyMuPDF's AGPL), `pillow` (PNG encoding for rendered pages — pypdfium2 produces raw bitmaps, not image files), `pyyaml`, `jsonschema` (provider-side validation of structured-output responses), the OpenAI-compatible client for the Foundry adapter, `azure-identity` (optional extra).
- Package name `osr-forge` (import `osrforge`) — both it and `osrforge` are unclaimed on PyPI as of 2026-07-08. MIT license; the package ships no game content (osrlib's OGL data stays in osrlib), and the release pipeline's dist audit machine-checks it.
- Released as 0.1.0 (Development Status :: 4 - Beta) via the tag-driven `release.yml`; documentation at <https://mmacy.github.io/osr-forge/>.

## Roadmap

1. **Phase 0 — skeleton and ground truth.** Package scaffold, contract types (report, overrides, run metadata), preprocessing, `FixtureProvider`, and a Foundry capability spike: confirm gpt-5.4's structured-output support, image limits, and context window, and record real fixtures from one short module.
2. **Phase 1 — extraction.** Survey + content passes against the Foundry adapter; get one full eval module extracting with credible per-area output and source pages.
3. **Phase 2 — a playable draft.** Monster mapping, geometry synthesis, assembly, `validate_adventure`, SVG previews. Milestone: a converted module loads and plays in osrlib's example TUI crawler.
4. **Phase 3 — the correction loop.** Overrides application, `rerun`/resume, playability lint, `estimate`, CLI polish. Milestone: fix a bad draft to publishable entirely through `overrides.yaml`.
5. **Phase 4 — measurement and release.** Eval corpus + scoring harness, docs, PyPI release. osr-web integration (its phase 4) starts here.
6. **Phase 5 — BYOM measurement.** Private eval corpora over locally owned modules: agent-authored truth under a pinned independence discipline (`tools/eval/AUTHORING.md`), partial (assertion-aware) truth, and a committed aggregate-only BYOM scoreboard. Milestone: scored numbers from disparate retail modules spanning a pinned diversity matrix, with no retail content in the repo.
7. **Phase 6 — playable structure.** Door and stair extraction and synthesis, survey site discipline, module metadata, treasure-grammar extensions, and overlap-based eval level alignment. Milestone: a multi-level module converts to a draft whose printed structure plays — every level reachable, doors that are doors — measured against the phase 5 baselines where the metrics reach, recorded as structural counts where they don't.
8. **Phase 7 — custom monster emission.** Adventure-bundled `MonsterTemplate`s extracted from printed stat blocks — an osrlib seam (shipped as osrlib 1.2) plus a stat-block pass, a pinned mapping policy, and the `monster_templates` override kind — with resolution measured on an extended metric. Milestone: a module's bespoke monsters convert to playable custom templates exercised on the committed corpus.

## Open questions

- **gpt-5.4 capabilities** (schema outputs, image inputs, context, cost) — phase 0 spike answers this and may reshape chunking.
- **Vision-first geometry.** A model pass that proposes *map-image* geometry — cell clusters and corridors matching the printed cartography — with the human still confirming. Narrowed by phase 6: prose-signal doors and stairs are extracted now, so the open question is the map image alone; needs the eval corpus to judge.
- **Custom item and spell emission.** Phase 7 built custom *monster* emission; the same story exists for `FeatureSpec.item_ids` and bespoke magic — deferred until a consumer or a measured gap demands it. The osrlib bundled-templates seam phase 7 consumed (osrlib 1.2's `Adventure.monsters`) is the template to copy.
- **Party and pregen extraction.** `adventure.json` has no party surface, so osr-web hardcodes a six-member roster and per-class equipment kits; osrlib already ships `party_to_document`. Consumer evidence recorded in phase 6; not yet scheduled.
- **Non-PDF inputs.** Markdown/HTML/EPUB modules would skip most of preprocessing; cheap to add once the pipeline stabilizes.
- **OCR fallback.** If model-side reading of low-quality scans underperforms, add a local OCR pass to populate the text layer.
- **Corpus diversity.** The committed, merge-gating corpus is a BFRPG monoculture (BF1 Morgansfort, the planned third member, failed the license-verification procedure — a credited contributor is absent from the relicensing consent list — and the held-out JN2 took its slot). The BYOM scoreboard (phase 5) is now the vehicle for general-performance numbers across disparate retail modules; what remains open is the reproducible gate itself — a non-BFRPG, *freely licensed*, license-verified member that any contributor can re-run, since contributors cannot sweep modules they don't own.
