# osr-forge specification

A standalone Python package and CLI that converts tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents. Input: a B/X-compatible module PDF. Output: a draft `adventure.json` validated against the real osrlib models, an extraction report describing what the pipeline was and wasn't sure about, and an overrides file through which humans correct the draft reproducibly.

osr-forge is front-end-agnostic by contract, not by implementation: it's Python (it must import osrlib to validate natively), but its consumers only need its artifacts — JSON and YAML files any stack can read — or its CLI. The first consumer is the osr-web app (`~/repos/osr-tui`), which wraps it in a conversion worker and builds a graphical review UI over the same contracts.

## Goals

- Convert a digital or scanned module PDF into a draft osrlib `Adventure` that passes `Adventure.model_validate`, with every gap and guess called out in a machine-readable report.
- Make human correction first-class and reproducible: corrections live in an overrides file, never in hand-edits to generated output, and re-assembly is a pure function of cached pipeline outputs plus overrides.
- Keep the LLM behind a small provider interface. The first adapter targets Azure AI Foundry with `gpt-5.4`; others (Anthropic, local models) are drop-ins later.
- Be usable three ways: as a CLI for humans, as a library for host apps (osr-web's worker), and as an artifact producer for any consumer that just wants the JSON.
- Ship quality evals so extraction changes are measured, not vibed.

## Non-goals

- **No GUI.** Review tooling here is artifacts: SVG map previews, the report, and the overrides file. Consumers build GUIs (osr-web's module workshop) on top of the same contracts.
- **No hosting or service.** It's a package + CLI; queueing, storage, auth, and multi-tenancy belong to host apps.
- **No custom game content.** Monsters resolve against the shipped osrlib SRD catalog; unresolvable monsters are flagged for a human-chosen substitute, not invented. (Custom `MonsterTemplate` emission is an open question.)
- **Not part of osrlib.** osrlib is frozen-API, pydantic-only, and sans-I/O; a converter with LLM and filesystem dependencies stays outside it and depends on it.
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
2. **Survey (1).** One structured-output request over the whole document (text + page images, chunked if needed): identify title, hooks, town/base info, the dungeon(s) and level(s), the keyed-area list per level with page locations, and every monster name that appears. Output is the index that plans the content passes.
3. **Content (2).** Per level, extract keyed areas in batches: description prose, encounters (monster name + count), traps, treasure, features, and connections to other areas ("the corridor continues north to area 7"), each with `source_pages` and a self-assessed confidence. Requests send only the relevant page images/text plus the survey index. Schemas are small and per-batch — one giant Adventure schema extracts worse and runs into structured-output limits.
4. **Monsters (3).** Resolve extracted names against `osrlib.data.load_monsters()` in tiers: normalized exact id match → curated alias table → stdlib fuzzy match (`difflib`) → one LLM pass that picks from the top-k candidates or answers "none of these". Unresolved names become report flags; `validate_adventure` rejects dangling ids, so publishing anywhere requires a human override choosing a substitute.
5. **Geometry (4).** Deterministic synthesis of grid geometry from the room graph: place the entrance area at the origin, walk the connection graph breadth-first, place rooms as rectangular cell clusters (10' per cell, sized from stated dimensions like "30' × 40'" when present, defaults otherwise) in the stated compass direction when known, route 1-cell-wide corridors, emit `Edge`s (open/door per the text), shift on collision. The output always satisfies osrlib's structural rules — entrance exists, transitions align, cells in bounds — even when it doesn't match the printed map. Every synthesized area is flagged `geometry_synthesized` in the report.
6. **Assemble and check (5).** Apply `overrides.yaml` to the cached stage outputs, build the `Adventure` via `model_validate`, run `validate_adventure` against the shipped monster/equipment catalogs, write the four artifacts. Validation failures land in the report rather than crashing — a draft is allowed to be invalid; *publishing* it somewhere is the consumer's gate.

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
│   └── monsters.json
├── overrides.yaml
├── previews/
│   └── <dungeon>.<level>.svg
├── report.json
└── adventure.json
```

Host apps archive the whole workdir (osr-web stores it in Blob Storage) to get debuggability and stage-level re-runs for free.

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

Supported override kinds in v1: monster remaps, per-area field replacement (description, encounter, trap, treasure, features), area add/remove, geometry (cells, edges, entrance, transitions), and town/module metadata fields. Overrides apply after stage outputs and before validation, so `check` always evaluates the corrected draft.

## Extraction report

`report.json` is regenerated on every assembly and is the complete input a review UI needs:

```json
{
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
  "monsters": { "resolved": 11, "unresolved": ["hobgoblin chieftain"] },
  "usage": { "input_tokens": 412000, "output_tokens": 88000 }
}
```

Flag vocabulary is small and enumerated (geometry synthesized, monster unresolved, low confidence, connection ambiguous, treasure unparsed, page unreadable) so UIs can badge reliably.

## Validation and playability checks

`check` runs two tiers against the assembled adventure:

- **Content validation:** `validate_adventure(adventure, load_monsters(), load_equipment())` — osrlib's own fail-fast gate for dangling references and structural problems.
- **Playability lint:** static graph checks the engine doesn't enforce but players feel — every area reachable from the entrance, no orphan cells, level transitions land on valid cells both ways, at least one non-secret path into each keyed area (secret-only access is a warning, not an error, since some modules intend it) — plus a smoke delve: build a seeded `GameSession` with a throwaway party, `EnterDungeon`, and execute a handful of `MoveParty`/`OpenDoor` commands along a computed path to prove the geometry actually plays. Full automated playthroughs are future work.

Findings are structured (id, severity, location, message) and merged into `report.json`.

## CLI

```text
osrforge convert <module.pdf> [--workdir DIR] [--provider foundry]
osrforge rerun <stage> [--workdir DIR]      # re-run one stage using cached upstream outputs
osrforge assemble [--workdir DIR]           # pure: stage outputs + overrides → artifacts
osrforge check [--workdir DIR]              # validate_adventure + playability lint
osrforge preview [--workdir DIR]            # regenerate the SVG maps
osrforge estimate <module.pdf>              # preprocess only; rough token/cost estimate
```

`convert` is `preprocess → survey → content → monsters → assemble` with per-stage status written to `run.json`; a failure stops there, keeps everything upstream, and `rerun` resumes. The intended human loop after conversion is: look at `report.json` and the previews → edit `overrides.yaml` → `osrforge assemble && osrforge check` → repeat until clean.

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

Provider settings come from an explicit settings object (library) or environment/config file (CLI): Foundry endpoint, deployment name, auth mode (key vs. Entra ID). Pipeline knobs with sane defaults: render DPI, max pages, content-pass batch size, fuzzy-match threshold, top-k for LLM monster matching. No global state.

## Testing and evals

- **Unit tests** cover the deterministic majority — preprocessing, geometry synthesis, override application, assembly, report generation, lint — with no network.
- **Pipeline tests** run end to end against `FixtureProvider` recordings, asserting byte-stable artifacts (assembly purity is itself a test).
- **Evals** are a separate, on-demand harness (not per-commit CI): a corpus of freely licensed adventures with hand-checked expected extractions, scored on area recall, encounter/monster accuracy, connection-graph correctness, and treasure accuracy. Extraction-prompt or model changes must not regress the scoreboard. The corpus ships pointers + hashes, not PDFs.
- **Compatibility:** golden `adventure.json` fixtures are loaded against the pinned osrlib on every CI run, so an osrlib upgrade that changes document semantics fails loudly here first.

## Tooling and packaging

- Python ≥ 3.14 (osrlib's floor), managed with `uv`; `ruff` format/lint, `pyright`, `pytest`.
- Dependencies: `osrlib` (compatible-range pin, `>=1.1,<2`), `pypdfium2` (rendering + text extraction; permissive license, unlike PyMuPDF's AGPL), `pyyaml`, the OpenAI-compatible client for the Foundry adapter, `azure-identity` (optional extra).
- Package name `osr-forge` (import `osrforge`) — both it and `osrforge` are unclaimed on PyPI as of 2026-07-08. MIT license; the package ships no game content (osrlib's OGL data stays in osrlib).

## Roadmap

1. **Phase 0 — skeleton and ground truth.** Package scaffold, contract types (report, overrides, run metadata), preprocessing, `FixtureProvider`, and a Foundry capability spike: confirm gpt-5.4's structured-output support, image limits, and context window, and record real fixtures from one short module.
2. **Phase 1 — extraction.** Survey + content passes against the Foundry adapter; get one full eval module extracting with credible per-area output and source pages.
3. **Phase 2 — a playable draft.** Monster mapping, geometry synthesis, assembly, `validate_adventure`, SVG previews. Milestone: a converted module loads and plays in osrlib's example TUI crawler.
4. **Phase 3 — the correction loop.** Overrides application, `rerun`/resume, playability lint, `estimate`, CLI polish. Milestone: fix a bad draft to publishable entirely through `overrides.yaml`.
5. **Phase 4 — measurement and release.** Eval corpus + scoring harness, docs, PyPI release. osr-web integration (its phase 4) starts here.

## Open questions

- **gpt-5.4 capabilities** (schema outputs, image inputs, context, cost) — phase 0 spike answers this and may reshape chunking.
- **Vision-first geometry.** A model pass that proposes geometry from the map image (human still confirms) could cut correction time a lot; needs the eval corpus to judge.
- **Custom monster emission.** Emitting non-SRD `MonsterTemplate`s for bespoke monsters instead of forcing substitutions — depends on consumers wiring osrlib's custom-catalog seam.
- **Non-PDF inputs.** Markdown/HTML/EPUB modules would skip most of preprocessing; cheap to add once the pipeline stabilizes.
- **OCR fallback.** If model-side reading of low-quality scans underperforms, add a local OCR pass to populate the text layer.
