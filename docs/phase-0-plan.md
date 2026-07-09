# Phase 0 plan — skeleton and ground truth

Implementation plan for phase 0 of [the osr-forge spec](spec.md). Phase 0 delivers the package scaffold (uv, ruff, pyright, pytest, CI, licensing), the contract types every later phase serializes (extraction report, overrides, run metadata), the deterministic preprocessing stage, the provider seam (`ModelProvider`, `FixtureProvider`, a recording wrapper, and the real `FoundryProvider`), and the Foundry capability spike: confirm gpt-5.4's structured-output support, image limits, and context window, and record real fixtures from one short module so phase 1's chunking and schema decisions rest on measured behavior instead of guesses.

## Scope

In scope:

- uv project layout, ruff/pyright/pytest configuration, GitHub Actions CI
- MIT license for code; a licensed test-content directory for the CC BY-SA spike module (mirroring osrlib's MIT/OGL split)
- `errors.py` exception hierarchy root
- Artifact schema versioning (`SCHEMA_VERSION` for osr-forge's own artifacts; adventure.json keeps osrlib's stamp)
- Contract types: `contracts/report.py`, `contracts/overrides.py`, `contracts/run.py`
- `settings.py` — `ConversionSettings` with the preprocessing knobs
- `workdir.py` and `preprocess.py` — stage 0, the only stage phase 0 implements
- `providers/` — the `ModelProvider` protocol and request/response types, `FixtureProvider`, `RecordingProvider`, `FoundryProvider`
- Test assets: an original CC0 mini-module PDF plus one short CC BY-SA module for the spike
- The Foundry capability spike, its findings document, and recorded fixtures
- Tests for all of the above, green in CI with no network

Out of scope (later phases): survey and content extraction — prompts, extraction schemas, chunking (phase 1); monster resolution, geometry synthesis, assembly, report *production*, SVG previews (phase 2 — phase 0 ships the report *types* only); overrides *application*, `rerun`/resume, playability lint, `estimate` (phase 3); the `osrforge` console script (arrives with the first end-to-end `convert`, phase 1 at the earliest; CLI polish is phase 3); eval corpus, scoring harness, docs site, PyPI release (phase 4). The report model's lint `findings` field is added (additively) by phase 3 with the lint itself.

Spec impact, applied in this PR: the spec's dependency list gains `pillow` (pypdfium2 renders to raw bitmaps; `PdfBitmap.to_pil().save()` is the supported PNG-encoding path — pypdfium2 writes no image files itself) and `jsonschema` (providers must validate responses against the request's JSON Schema; pydantic validates pydantic models, not arbitrary JSON Schema documents).

## Work items

### 1. Project scaffolding

- `pyproject.toml`: distribution `osr-forge`, import package `osrforge`, `requires-python = ">=3.14"` (osrlib's floor). Runtime dependencies: `osrlib>=1.1,<2` (1.1.0 is current), `pydantic>=2` (declared directly — contracts import it), `pypdfium2>=5` (v5 broke the v4 rendering API; targeting v5 from birth avoids a migration), `pillow` (PNG encoding), `pyyaml` (overrides), `jsonschema>=4` (response validation), `openai` (the OpenAI-compatible client for the Foundry adapter; the spike bumps the floor to the exact version it validates). Optional extra `entra`: `azure-identity` — key auth works without it; `DefaultAzureCredential` needs it. Dev group: `pytest`, `ruff`, `pyright`.
- src layout: `src/osrforge/` with `py.typed` from day one. `uv.lock` committed, `.python-version` pinned to 3.14, `.gitignore` for Python/uv artifacts and `*.forge/` workdirs.
- Module map, pinned so later phases slot in without renames:

    ```text
    src/osrforge/
    ├── __init__.py          # public façade; phase 0 exports ConversionSettings only
    ├── py.typed
    ├── errors.py            # OsrForgeError hierarchy
    ├── versioning.py        # SCHEMA_VERSION, osrforge_version()
    ├── settings.py          # ConversionSettings
    ├── workdir.py           # workdir layout paths + run.json I/O
    ├── preprocess.py        # stage 0
    ├── contracts/
    │   ├── report.py        # ExtractionReport + flag vocabulary
    │   ├── overrides.py     # Overrides models + YAML loading
    │   └── run.py           # RunMeta, Stage, StageStatus, TokenUsage
    └── providers/
        ├── base.py          # ModelProvider protocol, ModelRequest/ModelResponse, fingerprinting
        ├── fixtures.py      # FixtureProvider + RecordingProvider
        └── foundry.py       # Azure AI Foundry adapter
    ```

- The `__init__` façade re-exports only names the spec's library API promises (`ConversionSettings`, later `convert`/`assemble`/`check`/`estimate` as their phases land). Everything else is imported from its home module — one home per symbol; no convenience aliases.
- ruff: line length 120, Google docstring convention, import ordering per house style. pyright: `strict` on `src/`, `standard` on `tests/` — strict typing at the provider and contract boundaries is where it pays; test ergonomics matter more than test typing.

### 2. Licensing and test content

- `LICENSE`: MIT, covering all package code. The wheel ships no game content — test assets live under `tests/assets/`, outside the built distribution.
- `tests/assets/README.md` documents every asset's provenance and license. Two assets:
    - **`tests/assets/minimod/`** — an original mini-module authored for this repo, dedicated CC0: 4–6 pages, at least one text-layer page with keyed areas, stated room dimensions, and monster names (so phase 1 prompt work has an in-repo toy), and at least one image-only page with no text layer to exercise the scanned-module path. Under 300 KB. Authored once with any PDF-producing tool; the README records how. A second, tiny password-protected PDF exercises the encrypted-source error path.
    - **`tests/assets/<module>/`** — the spike module: a short (≤ 32 pages, single dungeon) Basic Fantasy RPG adventure. BFRPG relicensed to CC BY-SA 4.0 with its 4th edition (June 2023); candidate: JN1 The Chaotic Caves. **Verification is a spike step, not an assumption:** confirm the chosen module PDF's own license page states CC BY-SA 4.0 and its contributors appear on the project's consent list before committing anything. The directory carries the CC BY-SA 4.0 notice, attribution, source URL, and the PDF's sha256. This mirrors osrlib's split: MIT code, separately-licensed content, clearly fenced.

### 3. Continuous integration

- GitHub Actions on push and pull request: `astral-sh/setup-uv` with the committed lockfile, then `ruff format --check`, `ruff check`, `uv run pyright`, `uv run pytest`. Python 3.14, ubuntu + macos matrix — the second OS is not ceremony here: pypdfium2 ships platform-specific native wheels, so macos actually tests a different pdfium binary.
- No network in tests is an invariant, not an aspiration: CI carries no provider secrets, so any test that tries a live call fails loudly. The spike is the only live-network activity in phase 0 and runs manually.
- osrlib compatibility canary: a test that round-trips `stamp_document`/`check_document` with `kind="adventure"` and `model_validate`s a minimal hand-built `Adventure` (town, one dungeon, one level with an entrance and one area) against the pinned osrlib. This is the phase 0 stand-in for the spec's golden-fixture compatibility gate — real golden `adventure.json` fixtures arrive with assembly in phase 2; the canary still fails loudly if an osrlib upgrade moves the envelope or the models underneath us.

### 4. Errors and versioning

- `errors.py`: `OsrForgeError` base, `PdfError` (unreadable, encrypted, or limit-violating source), `ProviderError` (transport, auth, or rate-limit exhaustion), `SchemaValidationError(ProviderError)` (response failed the request schema after the retry budget), `FixtureMissError(ProviderError)`. The hierarchy grows additively; later phases add their own members. osrlib's rule adopted verbatim: programmer misuse raises stdlib `ValueError`/`TypeError`; the typed hierarchy is for runtime failures of the work itself.
- `versioning.py`: `SCHEMA_VERSION = 1` for osr-forge's own artifacts and `osrforge_version()` read from package metadata. `report.json` and `run.json` carry both as top-level `schema_version`/`osrforge_version` fields — the artifact contracts become osr-web's public API at integration time, so they are versioned from birth, additive-only within a version. Two envelopes deliberately not created: `adventure.json` is stamped by osrlib's `stamp_document` (kind `"adventure"`, osrlib's own `SCHEMA_VERSION`) and gets no second osr-forge wrapper; `overrides.yaml` is a human-authored input, not a produced artifact, and carries no version field in v1 — strict unknown-key rejection (below) means a future revision that needs one can add it detectably.

### 5. Contract types — `contracts/`

All contract models are frozen pydantic v2 models with `extra="forbid"` — osr-forge is the only *parser* of these files, and forbidding unknowns catches drift and overrides-file typos at load time. (Additive evolution still governs what we *emit* within a schema version; consumers reading artifacts are expected to ignore fields they don't know.)

- `contracts/run.py`:
    - `TokenUsage` — `input_tokens`/`output_tokens`, non-negative ints, with an addition helper. Lives here because run metadata is its primary artifact home; `report.py` and `providers/base.py` import it.
    - `Stage` — a `StrEnum` pinning the spec's stage names as wire values: `preprocess`, `survey`, `content`, `monsters`, `geometry`, `assemble`. These key `run.json`'s stage table and the `stages/` cache filenames; changing one later is a schema-version event.
    - `StageStatus` — `status` (`pending`/`running`/`completed`/`failed`), optional `error`, optional `started_at`/`finished_at` (ISO 8601 UTC), optional `usage`. Timestamps are legal here and only here: `run.json` is operational metadata. The pure artifacts (`adventure.json`, `report.json`, previews) must contain no timestamps, or assembly purity's byte-stability guarantee dies — the spec's report example contains none, and a test enforces it once report production exists (phase 2).
    - `RunMeta` — `schema_version`, `osrforge_version`, `source_sha256`, `source_bytes`, `page_count`, the settings echo, `provider`/`model_id` (null until a model stage runs), and `stages: dict[Stage, StageStatus]`.
- `contracts/report.py`:
    - `Flag` — a `StrEnum` of exactly the spec's vocabulary: `geometry_synthesized`, `monster_unresolved`, `low_confidence`, `connection_ambiguous`, `treasure_unparsed`, `page_unreadable`.
    - Serialized flags are plain strings shaped `<flag>` or `<flag>:<detail>` (the spec's example: `monster_unresolved:hobgoblin chieftain`). Pinned representation: an annotated string type validated against that grammar (prefix must be a `Flag` member; detail is free text), with parse/format helpers. Plain strings keep the JSON exactly as the spec shows it and let UIs badge on the prefix without a nested-object dance.
    - Area addressing: `AreaAddress` (dungeon id, 1-based level number, area key) with `parse`/`__str__` round-tripping the spec's `<dungeon-id>/<level-number>/<area-key>` form, plus a `LevelAddress` (`<dungeon-id>/<level-number>`) for geometry overrides. `/` is forbidden in dungeon ids and area keys (validated here; phase 1's extraction normalization enforces it at the source) — osrlib itself allows any string id, so the address grammar is only unambiguous because we constrain what we emit.
    - `ExtractionReport` mirroring the spec example: `module` (title, pages), `validation` (passed, errors), `areas` (id, `source_pages`, `confidence` in [0, 1], `flags`, `overridden`), `monsters` (resolved count, unresolved names), `usage`, plus the versioning pair from work item 4. Phase 0 ships the model and its round-trip tests; nothing produces a report until phase 2.
- `contracts/overrides.py`:
    - Top-level keys pin the spec's v1 override kinds: `monsters`, `areas`, `geometry`, `town`, `module`. Every entry carries a required, non-empty `reason`.
    - `monsters`: extracted name → `{template_id, reason}`.
    - `areas`: `AreaAddress` string → `AreaOverride` with optional `name`, `description`, `encounter`, `trap`, `treasure`, `features`, and `remove: bool`. The replacement payloads are osrlib's own models (`KeyedEncounter`, `TrapSpec`, `AreaTreasureSpec`, `FeatureSpec`) embedded directly — never re-declared — so an override that osrlib would reject fails at overrides *load* time, not assembly time. Field semantics pinned: **absent means untouched; explicit `null` means clear** (remove the trap vs. don't touch the trap). Pydantic's unset-vs-None distinction models this exactly. An entry addressing an area the draft doesn't have is an area *add* and must carry the full required payload — enforced at application time (phase 3), since only assembly knows what the draft contains.
    - `geometry`: `LevelAddress` string → per-area `cells`, `edges`, optional `entrance` and `transitions` (osrlib `Edge`/`TransitionSpec` payloads). Hazard, pinned for phase 3: override edge keys accept any `x,y:direction` of the four directions, but osrlib's canonical `edge_key` form stores only `north`/`west` keys (a cell's east edge is its eastern neighbor's west edge). The spec's own example, `"5,2:east"`, is non-canonical. Application (phase 3) canonicalizes via osrlib's `edge_key`; phase 0's model validates the `x,y:direction` grammar only.
    - `town` / `module`: the mutable metadata fields (`TownSpec` fields; adventure name/description/hooks), one `reason` each.
    - Loading: `yaml.safe_load` only, then model validation; a missing file loads as the empty `Overrides`. Duplicate YAML keys are a silent-last-wins footgun for a human-edited correction file — if pyyaml's default loader can't be made to reject them cheaply, note it as a known gap for phase 3's application work rather than gold-plating now.
- Artifact serialization pinned once, in `workdir.py`, for every JSON artifact: `model_dump(mode="json")`, UTF-8, 2-space indent, keys in model-declaration order (no sorting — pydantic order is deterministic), trailing newline. Byte-stability across identical runs is the property pipeline tests will assert from phase 2 on; pinning the writer now means those tests never chase formatting noise.

### 6. Provider seam — `providers/base.py` and `providers/fixtures.py`

- Request/response types, exactly as narrow as the spec's protocol:
    - `TextPart` (`text`) and `ImagePart` (`png` bytes — preprocessing emits PNG; adapters do their own base64/data-URL packaging).
    - `ModelRequest`: `tag` (a short stable label like `survey` or `probe.image-limits` — names fixture files and attributes usage), `system`, `parts` (ordered text/image parts), `schema` (a JSON Schema dict the response must satisfy).
    - `ModelResponse`: `data` (parsed, schema-valid JSON), `usage: TokenUsage`, `model_id`.
    - `ModelProvider`: a `runtime_checkable` `Protocol` with the single `generate(request) -> response` method. Pipeline code never imports a vendor SDK — the invariant from AGENTS.md, testable because `providers/foundry.py` is the only module allowed to import `openai`/`azure.identity`.
- Schema enforcement is the provider's contract: `generate` either returns `data` that validates against `request.schema` (checked with `jsonschema`) or raises `SchemaValidationError` after its retry budget. Callers trust `response.data`.
- Request fingerprinting, shared by fixtures and future run-caching: canonical JSON (sorted keys, compact separators, UTF-8) of the request with each image part replaced by `{"sha256": ..., "bytes": ...}`; the fingerprint is the sha256 hex of that canonical form, exposed as `ModelRequest.fingerprint()`. `tag` participates in the fingerprint (it's part of request identity; tags are stable stage names, not free prose).
- `FixtureProvider(fixture_dir)`: replays recordings. File naming `<tag>.<fingerprint[:12]>.json`; the file carries `schema_version`, the full fingerprint (verified on load), the tag, a human-reviewable request digest (system text and text parts verbatim, images as sha256+size — so fixture diffs are readable in PRs), and the full response. Lookup is by fingerprint only; a miss raises `FixtureMissError` naming the tag, fingerprint, directory, and the tags it *does* have. Replayed `data` is re-validated against the incoming request's schema, so a prompt/schema change against stale fixtures fails as a clear `SchemaValidationError`, not a silent wrong answer.
- `RecordingProvider(inner, fixture_dir)`: pass-through that writes each exchange as a fixture file (idempotent overwrite by fingerprint). This is how the spike records, and later how evals re-record. It lives in the package, not in `tools/`, because host apps and evals need it too.
- Fixture/rendering coupling hazard, and the decision that defuses it: fingerprints hash image bytes, and PNG bytes are stable only for a locked pdfium+Pillow pair — a dependency bump would strand every recorded fixture if tests re-rendered pages at test time. So committed fixtures are always paired with the committed page assets they were recorded against (`tests/assets/<module>/pages/`), and pipeline tests build requests from those committed PNGs, never from a fresh render. Preprocessing tests assert rendering *works* (dimensions, content smoke) but never byte-compare PNGs.

### 7. Foundry adapter — `providers/foundry.py`

The spec's phase 0 entry lists `FixtureProvider`; recording *real* fixtures requires the real adapter, so `FoundryProvider` lands here too — minimal but real, and the spike doubles as its live integration test. Phase 1 hardens whatever the spike proves shaky.

- `FoundrySettings`: `endpoint`, `deployment`, optional `api_key`, optional `api_version`; `from_env()` reads `OSRFORGE_FOUNDRY_ENDPOINT`, `OSRFORGE_FOUNDRY_DEPLOYMENT`, `OSRFORGE_FOUNDRY_API_KEY` (an `OSRFORGE_` prefix, not `AZURE_OPENAI_*`, to avoid colliding with other tools' env conventions — the README documents the mapping). Auth mode is inferred: key present → key auth; absent → Entra ID via `DefaultAzureCredential` (`azure-identity` imported lazily; a missing import raises `ProviderError` naming the `osr-forge[entra]` extra).
- Built on the `openai` package's Azure surface. Whether the deployment speaks the `AzureOpenAI` client's api-version dialect or the newer `/openai/v1` base-URL surface is a spike question; the adapter pins whichever the spike validates and records the choice in the findings doc.
- Structured output: native JSON-schema response format when the spike confirms gpt-5.4 supports it; otherwise validate-and-retry (parse, `jsonschema`-validate, on failure re-prompt once with the validation errors appended, budget of 2 schema retries, then `SchemaValidationError`). Either way the provider owns enforcement — the pipeline never sees invalid `data`.
- Transport policy: bounded exponential backoff on 429/5xx honoring `Retry-After`; auth and 4xx failures raise `ProviderError` immediately. Images ship as base64 PNG data URLs.
- Testability without network, pinned: the constructor performs no I/O, and the underlying client is injectable (`FoundryProvider(settings, client=...)`). Unit tests assert the request → chat-payload mapping (message structure, image data URLs, response-format block), response parsing, usage extraction, and retry classification against a stub client. Live behavior is the spike's job.

### 8. Settings — `settings.py`

- `ConversionSettings` (frozen, `extra="forbid"`): `render_dpi = 150` (spec default), `max_pages = 200`, `max_source_bytes = 100 MiB`. The page cap is roughly 3× the largest plausible B/X module — a guardrail against wrong-file mistakes, not a real constraint; both limits exist so a host can surface "this file is not a module" before any work happens. Later knobs (content-pass batch size, fuzzy threshold, top-k) are added by the phases that consume them — an unread setting is dead accommodation.
- The settings echo written into `run.json` is the full `model_dump` of `ConversionSettings`, so `rerun` (phase 3) can detect settings drift between stages.

### 9. Workdir and preprocessing — `workdir.py`, `preprocess.py`

- `Workdir`: one class owning the spec's layout — `source.pdf`, `run.json`, `pages/`, `stages/`, `overrides.yaml`, `previews/`, `report.json`, `adventure.json` — plus `run.json` read/write and the pinned JSON writer from work item 5. No other module builds these paths by hand.
- `preprocess(pdf_path, workdir_path, settings) -> RunMeta`, deterministic code only:
    1. Stat the source; over `max_source_bytes` → `PdfError` before reading further. Copy the source into the workdir as `source.pdf` and sha256 it.
    2. Open with pypdfium2 (v5 API). Corrupt or password-protected sources → `PdfError` with the cause chained; encrypted PDFs are unsupported in v1.
    3. Page count over `max_pages` → `PdfError`.
    4. Per page, 1-based, zero-padded to 4 digits (`0001.png` per the spec's layout; `max_pages` keeps 4 digits sufficient): render at `scale = render_dpi / 72` → `bitmap.to_pil().save(...)` as RGB PNG; extract the text layer via `get_textpage().get_text_range()`, normalize newlines to `\n`, write `NNNN.txt` UTF-8 — an empty or missing text layer yields an empty file (the spec's scanned-module behavior), never a skipped one.
    5. Close pdfium handles deterministically (`finally`/context managers) — they hold native memory and page loops leak without it.
    6. Write `run.json`: preprocess `completed` with page count, hashes, settings echo; all other stages `pending`.
- Re-running preprocess on an existing workdir rebuilds it: `pages/` is cleared before rendering so a shorter re-render never leaves stale trailing pages. Skip-if-unchanged logic belongs to `rerun` (phase 3), not here.
- PNG byte-stability across pdfium/Pillow versions is explicitly *not* a contract (see the fixture-coupling decision in work item 6). Assembly purity begins at the cached stage outputs.

### 10. The Foundry capability spike

Manual, live-network, run from `tools/spike/` scripts (repo-only, never packaged, never in CI) that drive the real package: `preprocess()` on the spike module, then probes through `RecordingProvider(FoundryProvider(...))`.

- **Module selection and licensing gate** (work item 2's verification step) precedes any recording. Commit the PDF, its preprocessed page assets, attribution README, and hashes.
- **Probes**, each a recorded fixture:
    1. Structured output: a trivial schema; a survey-shaped schema (nested arrays, enums, per-area objects); then progressively larger/stranger schemas to find size and keyword limits (`$defs`, `additionalProperties: false`, enum cardinality). If native JSON-schema mode is absent or crippled, that's a finding, and the adapter's validate-and-retry path becomes the default.
    2. Image input: one page at 150 DPI; then 4/8/16/32 pages per request until refusal or quality collapse; note per-page image token cost at 100/150/200 DPI (this seeds phase 3's `estimate` heuristics).
    3. Context: the whole module (text + images) in one request if limits allow; otherwise bisect to the practical ceiling.
    4. Extraction credibility smoke: one survey-shaped and one content-shaped request over real pages, output eyeballed for sanity. These recordings are phase 1's seed corpus, not a quality bar — the eval harness (phase 4) owns measurement.
    5. Usage and cost: usage fields present and plausible; record the deployment's published pricing.
    6. Auth: both key and Entra paths exercised once each.
- **Deliverable**: `docs/foundry-capabilities.md` recording the model id string the service returns, the API surface used, every limit found with the probe that found it, observed costs, quirks — and a closing "phase 1 impacts" section stating the survey/content chunk sizes and schema budget the findings support. This answers the spec's first open question; if the answers reshape chunking assumptions, that's spec/phase-1-plan input, recorded there, not silently absorbed.

### 11. Tests

- **contracts**: round-trip (`model_validate(model_dump())`) for report, run, and overrides models; flag-grammar accept/reject table (bare flag, flag-with-detail, unknown prefix rejected); `AreaAddress`/`LevelAddress` parse/format round-trips and `/`-in-id rejection; the spec's own overrides example (§ Overrides) parses verbatim — keeping the spec and the models honest against each other; `reason` required and non-empty on every override kind; unknown top-level and per-entry keys rejected; absent-vs-null distinction on `AreaOverride` fields observable after a load/dump cycle.
- **preprocess** (against `minimod`): page count and `NNNN` numbering; the text page's `.txt` contains expected strings; the image-only page yields an empty `.txt` that exists; PNG dimensions match page size × dpi/72 within rounding; `max_pages=1` and a tiny `max_source_bytes` each raise `PdfError`; the encrypted asset raises `PdfError`; re-run after truncating settings leaves no stale pages; `run.json` carries the right hash, page count, settings echo, and stage statuses.
- **providers**: fingerprint goldens — dict-key order and YAML/JSON insertion order don't change the fingerprint, image-byte substitution does what work item 6 says, `tag` changes the fingerprint; record → replay round-trip through `RecordingProvider` then `FixtureProvider` returns the identical response; fixture miss raises `FixtureMissError` with the pinned diagnostics; replayed data failing the incoming schema raises `SchemaValidationError`; both providers satisfy `ModelProvider` (`isinstance` under `runtime_checkable`).
- **foundry (stubbed)**: request mapping, response parsing, retry classification, and settings-from-env (including the missing-`azure-identity` error path) against an injected stub client — zero network.
- **compat canary**: the osrlib round-trip from work item 3.
- All green under `uv run pytest` locally and in CI on both OSes.

## Sequencing

1. Scaffolding, licensing, CI — CI green on a trivial test before any real code lands.
2. `errors.py` and `versioning.py` — everything else needs them.
3. `contracts/` — pure models, no I/O dependencies; the spec-example tests pin the wire formats early.
4. `settings.py`, `workdir.py`, `preprocess.py`, and the `minimod` asset — first real I/O, first stage.
5. `providers/base.py` and `providers/fixtures.py` — the seam and its offline half.
6. `providers/foundry.py` with stubbed tests.
7. The spike: license-verify and commit the module, record fixtures and page assets, write `docs/foundry-capabilities.md`.
8. README (dev quickstart: `uv sync`, `uv run pytest`; env vars; licensing notes) and a final pass tracing every phase 0 spec contract to code and tests or to a named deferral.

## Definition of done

- `uv sync && uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest` passes locally and in CI on both OSes, with no network use in any test.
- `preprocess()` on the committed mini-module produces the spec's workdir layout — paired `NNNN.png`/`NNNN.txt` for every page, the image-only page's text file empty, `run.json` with source hash, settings echo, and per-stage statuses.
- A fixture recorded through `RecordingProvider` replays through `FixtureProvider` byte-identically and schema-validated, demonstrated in tests without network.
- `docs/foundry-capabilities.md` answers the spec's gpt-5.4 open question (structured output, image limits, context, cost, auth) with a recorded fixture behind every claim, and states its phase 1 impacts.
- Real fixtures from the license-verified spike module are committed with attribution, alongside the exact page assets they were recorded against.
- Every phase 0 item in the spec's roadmap entry is traceable to code and tests here, or explicitly deferred above with its phase named.
