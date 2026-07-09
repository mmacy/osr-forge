# Foundry capabilities: gpt-5.4

Findings from the phase 0 capability spike (2026-07-09), answering the spec's
first open question. Every claim traces to a recorded fixture in
`tests/assets/chaotic-caves/fixtures/` (named per claim below); the probes are
`tools/spike/probes.py`, run against the real deployment over the
license-verified spike module *JN1 The Chaotic Caves* (48 pages).

## The deployment

- Model: **gpt-5.4**, model version 2026-03-05; the service returns
  `model_id: "gpt-5.4-2026-03-05"` on every response (all fixtures).
- Hosting: Azure AI Foundry / Azure OpenAI resource, GlobalStandard SKU,
  eastus2.
- API surface: the `openai` package's `AzureOpenAI` client (api-version
  dialect), **api-version `2024-10-21`** — `FoundrySettings`' default worked
  unchanged, so the adapter keeps it. The newer `/openai/v1` base-URL surface
  was not needed and was not exercised.
- Client library: `openai` 2.44.0 validated live; `pyproject.toml`'s `>=1`
  floor is bumped to `>=2.44` with this document.

## Structured output

Native JSON-schema response format (`response_format: {type: "json_schema"}`,
non-strict) is **accepted, honored in practice, but not guaranteed**:

- A trivial schema, a survey-shaped schema (nested arrays, enums, `$defs`,
  `additionalProperties: false`), and stress schemas up to **512-value enums
  with 8-level `$ref` nesting** (`probe.schema-stress-e512-d8`; ~9 KB
  canonical schema, 21 KB as a pretty-printed fixture) all returned
  schema-valid JSON on the first attempt (fixtures: `probe.trivial`,
  `probe.survey-schema`, `probe.schema-stress-e16-d2`, `-e128-d4`,
  `-e512-d8`).
- At **2000-value enums with 16-level nesting** the model returned
  schema-invalid JSON (a missing required property) on all three adapter
  attempts. That failure confounds enum size with nesting depth, so neither
  dimension's individual ceiling is pinned — only that the combination is past
  it. The service accepted the oversized schema without a 400 — it did
  not hard-enforce it — so the failure surfaced as the adapter's
  `SchemaValidationError`. No fixture exists for this probe because recording
  persists only successful exchanges; the probe log is the evidence, and the
  probe is re-runnable.
- Consequence, already implemented: **the adapter's validate-and-retry loop is
  load-bearing, not belt-and-suspenders.** Native mode alone is not a
  guarantee of schema compliance at any size.

## Image input

- **32 page images in one request** (150 DPI letter pages) with no refusal and
  no quality collapse: asked for the highest printed page number visible, the
  model answered correctly at 1, 4, 8, 16, and 32 pages
  (`probe.image-count-01` … `-32`). The practical ceiling was not reached;
  32 pages is the highest count probed.
- **~905 input tokens per 150-DPI letter page** — measured 970 tokens for
  1 page and 29,087 for 32 pages of the same request shape, i.e. linear at
  ~905/page after the ~65-token text overhead.
- **DPI does not change image token cost**: the same page at 100, 150, and
  200 DPI cost an identical 962 input tokens (`probe.dpi-cost-100`/`-150`/
  `-200`). The tokenizer evidently normalizes resolution in this range, so
  render DPI is a legibility knob, not a cost knob. The spec's 150 DPI default
  stands; raising it for hard-to-read scans costs nothing in tokens.

## Context

- The **whole 48-page module — every page's text plus every page's image —
  fit in one request: 85,353 input tokens** (`probe.context-48pages`), and the
  model's summary of it was accurate. No bisection was needed.
- Azure's published pricing tiers imply a **272K-token input tier** before
  prices double, so whole-module requests have head-room up to roughly
  150–170 pages at ~1,600 tokens/page (image + text). The spec's 200-page cap
  can exceed the cheap tier; that is a cost cliff, not a hard limit.

## Usage and cost

- `usage` fields (prompt and completion tokens) were present and plausible on
  every response; the adapter accumulates them across schema-retry attempts.
- Published pricing (Azure OpenAI, GlobalStandard, ≤272K-token requests, as of
  2026-07-09): **$2.50 / 1M input, $0.25 / 1M cached input, $15.00 / 1M
  output**; above 272K input: $5.00 / $22.50. Source:
  [Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/).
- Observed spike cost: two full probe runs ≈ 350K input + 13K output ≈ **$1.10
  total**. A whole-module single request for this 48-page module costs ~$0.21
  of input.

## Extraction credibility

Survey-shaped and content-shaped requests over the 8 committed pages (town key,
caves key, keyed map) produced credible output (`probe.extract-survey`,
`probe.extract-content`): correct area keys and names, correct `source_pages`,
encounters with counts, and self-assessed confidence values in [0, 1]. These
fixtures are phase 1's seed corpus, not a quality bar — the eval harness
(phase 4) owns measurement.

## Auth

Both paths work against the live deployment, each behind its own fixture
(`probe.auth-key`, `probe.auth-entra`): API key, and Entra ID via
`DefaultAzureCredential` (picked up the Azure CLI credential; the identity
needs a data-plane role such as Cognitive Services OpenAI User). The requests
differ per path so the fixtures record separately — a fixture proves its
request succeeded, not which credential carried it, so the probe log remains
the evidence that each mode was exercised.

## Quirks

- Responses arrive as ordinary chat completions; `choices[0].message.content`
  held exactly one JSON document in every probe — no fencing, no prose.
- The e2000-d16 stress schema was accepted by the service without complaint
  and then not honored — treat "request accepted" and "schema enforced" as
  unrelated facts.
- 100-DPI renders were sufficient for the model to read page headings, but
  every recorded extraction ran at 150 DPI (the default).

## Phase 1 impacts

- **Survey chunking**: a whole typical B/X module (≤ ~64 pages) fits one
  survey request comfortably (48 pages ≈ 85K tokens, measured). Plan the
  survey as a single request up to a ~150-page/~240K-token guard, chunking
  only beyond that; chunk boundaries are a cost cliff (the >272K tier), not a
  correctness cliff.
- **Content batching**: 8-page batches (~16K input) produced clean per-area
  output at ~$0.05/batch input. Batch size can be tuned for prompt-focus, not
  for limits — even 32-page batches are proven safe on the image side.
- **Schema budget**: keep extraction schemas at or below the proven
  e512-d8 scale (~9 KB canonical, enums ≤ 512, nesting ≤ 8 `$ref` levels) —
  far above what survey/content schemas need. Do not lean on native
  enforcement; the validate-and-retry path stays mandatory.
- **`estimate` heuristics (phase 3)**: input ≈ pages × (~905 image tokens +
  text-layer tokens) regardless of DPI in 100–200; content-pass output ≈
  550 tokens/page observed on dense key pages.
- **No spec change needed**: preprocessing's text+image page model and the
  spec's 150 DPI default are validated as-is; chunking assumptions in the spec
  survive contact with measured behavior.
