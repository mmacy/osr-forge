# osr-forge improvement specification

The second roadmap arc: four numbered phases (9–12) and three companion workstreams that raise conversion accuracy where the eval harness says it is actually lost and remove the correction loop's measured friction. This document is the arc-level authority — scope, sequencing, and rationale; each phase still ships as two PRs (plan, then implementation) through the phase loop, and each phase plan remains the decision-complete contract for its implementer. `docs/spec.md` stays the contract authority for the artifacts and the pipeline; where this arc changes a contract, the change lands in `docs/spec.md` with the implementing PR, exactly as prior phases did.

Evidence base: the committed scoreboard's phase 7 double sweep (2026-07-17, `gpt-5.4-2026-03-05`, osr-forge 0.1.0) and a four-repo survey (osr-forge, osrlib-python, osr-web, osr-editor) conducted 2026-07-30.

## Where quality is lost today

Area *discovery* is solved — recall 1.0 on all three committed members, dungeon alignment exact on both sweep runs. The remaining losses concentrate in structure and identity:

| metric | JN1 | JN2 | minimod | reading |
| --- | --- | --- | --- | --- |
| connection F1 | 0.6436 | 0.8511 | 0.6667 | the weakest family; JN1 edge precision is 0.5833 — the pipeline *invents* edges |
| custom accuracy | 0.2857 | 0.72 | — | bespoke creatures mis-resolved to SRD templates instead of emitting their printed blocks |
| resolution accuracy | 0.8824 | 0.8519 | 1.0 | roughly one keyed resolution in eight is wrong |
| name recall | 0.9083 | 0.9123 | 1.0 | encounters the content pass never surfaced |
| area precision | 1.0 | 0.9149 | 1.0 | JN2 extracted eight areas the module doesn't key |

Behind the numbers, four root causes, each with a specific mechanism:

- **The printed map is never read.** Geometry is synthesized from prose connections alone — rectangles sized by a dimension regex (2×2 default), BFS placement, 1-cell corridors — and the map image rides content requests only as a compass-direction hint. Doors realize only on tree-edge routes; a door-bearing connection that closes a cycle drops its door with a flag (`_door_edges` in `geometry.py`). The map, the module's authority on adjacency and doors, is exactly the evidence the weakest metric family lacks.
- **Resolution prefers a plausible pick over a null.** The LLM tier chooses among the top-8 `difflib` candidates and a wrong pick carries no flag — the spec's own "flagless wrong pick." JN1's custom accuracy of 0.2857 is this mechanism: bespoke creatures confidently mapped onto SRD neighbors, which also poisons resolution accuracy. The stat-block pass — which reads the printed AC and HD — runs only *after* resolution, over only the names resolution gave up on, so the one signal that could veto a wrong pick arrives too late to.
- **Single-pass extraction with no second opinion.** The survey's known catastrophic failure (the "mode-flip": ten separate lairs collapsing into one dungeon, historically about one run in four before phase 6's prompt change) is undetectable from the artifacts of the run it happens in. The content pass gets exactly one retry, and only for keys a batch skipped.
- **Confidence is recorded and never used.** `AreaReport.confidence` is cached and echoed into the report, but `low_confidence` flags fire only on structural conditions — a 0.05-confidence area carries no badge. Hallucinated areas (JN2's eight) and missed encounters are visible to the eval harness and invisible to the user.

And two measurement blind spots hide exactly the things phases 10–12 change: vertical transitions and door facts are not scored at all (the connection metric covers undirected same-level edges only), and there is no encounter-precision metric (truth cannot yet distinguish asserted-empty from unasserted, so hallucinated encounters cost nothing).

## Principles

1. **Measure before moving.** Phase 9 extends the yardstick before any phase touches extraction. A quality change the harness cannot score does not ship.
2. **No new silent guesses.** Every inference this arc adds — a map-proposed edge, a stat-veto, a census disagreement — lands with a flag or a finding. Disagreement between evidence sources is surfaced, never resolved silently. This is the correction loop's contract: the report is the complete list of what to review.
3. **The map is evidence, not authority.** Map-reading output is a cached stage output like any other extraction; reconciliation with prose is deterministic assembly-time code; the human override remains the last word. Assembly purity is untouched — LLM calls stay in extraction stages.
4. **One sweep per phase.** The eval regression rule prices every prompt/schema edit at a live double sweep plus fixture re-record. Each of phases 10–12 therefore lands its model-facing edits as one implementation PR with one sweep, and all truth-semantics changes for the whole arc front-load into phase 9 — phases 10–12 add no truth keys, so their sweeps score against a stable yardstick.
5. **Additive contracts, kept additive.** New flags, findings, report fields, settings knobs, and truth assertion keys are additive within the current schema versions. The artifact fence is owner-suspended, but this arc does not spend that waiver — nothing here renames or re-shapes an existing contract surface.
6. **Repo boundaries hold.** Validation logic moves *up* into osrlib where downstream projects have re-implemented it; extraction never does. Consumer work is recorded here as asks and specified in each consumer's own repo under its own review discipline.

## Phase 9 — measurement extension

The arc's yardstick. Scorer and truth-schema work only — no extraction prompt or schema changes, so the existing members re-measure offline: re-score the standing phase 7 sweep pair from its retained workdirs, refresh the band table, record the pair in the phase amendment. The one live-spend exception is the contingent new corpus member below, which has no retained workdirs and buys its own baseline the standard way.

In scope:

- **Door, transition, and entrance metric families.** New truth assertion keys for door facts on asserted connections (presence and kind; whether stuck/locked state is asserted is a plan decision), for vertical links (stairs/trapdoor/chute edges between levels), and for the entrance (the printed key of the area holding each dungeon's entrance), scored assertion-aware exactly like connections — an omitted key asserts nothing. These are the metrics phases 11–12 are judged on; the entrance assertion exists because phase 11's map-reading stage proposes an entrance, and principle 1 forbids shipping a proposal the harness can't score.
- **Encounter precision.** A truth convention distinguishing asserted-empty (`encounters: []` — this area has none) from unasserted (key omitted), mirroring the connections/treasure pattern, plus the precision metric over the asserted universe — the hallucination guard phase 5 deferred for exactly this missing convention.
- **The in-phase truth passes.** New assertion keys are a truth-semantics migration, so the passes over every corpus member — committed and the owner's private BYOM corpus alike — complete inside this phase: agent-authored from the printed pages under `tools/eval/AUTHORING.md`, adversarially verified in fresh contexts, provenance recorded in the manifests. The owner-sampling delta leaves the phase as a GitHub issue assigned to the module owner, per the standing obligation. (BYOM board publication continues to wait on the already-open owner-sampling leg; these passes don't change that.)
- **A non-BFRPG committed member, contingent.** Run the phase 0 license-verification procedure over freely licensed non-BFRPG candidates; a member that passes joins the corpus with full truth (including the new families) and its own live double sweep — joining is "a manifest, a truth file, and a sweep," and a member with no retained workdirs cannot be scored offline. If none passes, the phase records the failed candidates the way phase 4 recorded BF1, and the open question stays open — the contingency is explicitly not a blocker on the phase.

Milestone: door, transition, entrance, and encounter-precision quality are measured numbers on every corpus member — with noise bands on the committed corpus, advisory scores on the BYOM board's members — before any extraction change ships; the existing members score offline against the retained standing pair.

## Phase 10 — resolution and survey hardening

Kill the silent failure modes that don't need the map. One implementation PR, one fixture re-record, one live double sweep.

In scope:

- **Stat-aware resolution.** The stat-block pass extends to cover names the LLM tier resolved (today it covers only unresolved names), and a deterministic comparator — printed HD and AC against the candidate template's, tolerances pinned in the plan — becomes a veto: a mismatched LLM pick resolves to null instead, flowing into the existing custom-emission path, and a near-miss ships flagged. The principle pinned here, with tier coverage (whether fuzzy-tier accepts are also gated) a plan decision: **no non-exact resolution ships unflagged when a printed stat block contradicts it.** This attacks custom accuracy and the flagless wrong pick with one mechanism — resolution accuracy it *protects* rather than attacks (the phase 10 plan's measurement: every resolution miss on the corpus is a false null the veto cannot reach, so the tolerance is pinned to protect the truth-confirmed non-exact picks) — and its cost is bounded by the LLM tier's residue (the exact/alias/fuzzy tiers resolve most names without it).
- **Survey self-consistency.** A second, cheap census request (site list and key ranges only) after the survey; a deterministic comparison of the two site sets; disagreement lands as a module-scope flag naming both readings. Whether disagreement also triggers one bounded re-roll is a plan decision; the flag is not. This makes the mode-flip visible in the run it happens in instead of at the next eval sweep.
- **Confidence thresholding.** Extraction confidence below a floor (pinned in the plan against the corpus's observed confidence distributions) emits `low_confidence` with the value in the detail, so the report's badge vocabulary finally carries the model's own uncertainty.
- **New flag vocabulary, additive** (names pinned in the plan), with the consumer-adoption note recorded in workstream C — osr-editor's review queue mirrors the flag list in TypeScript and picks the new badges up with a one-file change.

Milestone: a wrong resolution can no longer be silent — every non-exact pick is either stat-confirmed or flagged — and a mode-flipped survey flags itself; measured on the committed corpus with resolution, custom, and the phase 9 families all inside or above their bands except where the PR justifies movement.

## Phase 11 — map-grounded structure

The map image becomes extraction input for the first time. One new model stage, one deterministic reconciliation, one sweep.

In scope:

- **A map-reading stage.** A new cached extraction stage (position in the stage order, cache filename, and rerun/knob surface pinned in the plan) that reads each level's map page(s) and proposes, per level: room-key adjacency pairs, door positions and kinds on those pairs, and the entrance — and nothing more. Room footprints are deliberately *not* in this stage's output: that schema extension belongs to phase 12, and building it here for a contingent phase is exactly the dead accommodation the greenfield discipline forbids. Output is a raw cached stage artifact like every other extraction output; the stage slots into `rerun` and the settings knob→stage map; `estimate` learns its cost.
- **Deterministic reconciliation.** Geometry synthesis consumes prose connections and map proposals together under a pinned precedence policy: agreement strengthens (and rescues the doors currently dropped on cycle-closing routes — the map gives them a wall to land on); a map-only or prose-only edge, and any door/kind disagreement, synthesizes under a pinned default and carries a flag naming both readings. The entrance gets the same treatment: today's entrance is a placement heuristic, a map-proposed entrance that disagrees with it synthesizes under a pinned default and carries a flag, and phase 9's entrance assertion is what scores the outcome. No proposal is adopted silently.
- **Consumer surface.** The disagreement flags ride the existing report contract into osr-editor's review queue; the preview upgrades that make map-vs-preview comparison humane (coordinate grid, side-by-side index) ship earlier in workstream A and are assumed here.

Milestone: every map/prose disagreement in the committed corpus is a visible report item, and the phase 9 door, transition, entrance, and connection metrics — especially JN1's edge precision — are re-measured with the sweep; the PR carries the reading of whether reconciliation moved them beyond the bands.

## Phase 12 — map-grounded shape, contingent

Room footprints from the map: the map-reading stage's schema grows footprint proposals (phase 12's extension, not phase 11's — see above), and where a room's cell footprint is proposed confidently, synthesis uses it instead of the dimension-regex rectangle; corridors route to match proposed topology. Everything else about the phase — schema, flags, the confidence bar, override interplay — is pinned by its plan.

This phase is explicitly contingent: phase 11's sweep is the go/no-go evidence, and the reading — go or no-go, with the metric movements it rests on — is recorded in this document as an amendment before any phase 12 plan is written. If reconciliation did not move the structure metrics beyond their bands, the premise (the model can read these maps well enough to trust with shape) is re-examined in that same amendment.

Milestone: phase 9's structure metrics hold or rise with footprints in play, and the phase amendment records a structural-counts comparison of synthesized footprints against the modules' printed dimension statements — the phase 6 convention for what the metrics don't reach, recorded in the amendment rather than asserted in truth files, so phases 10–12 keep adding no truth keys. The aim behind the numbers — geometry overrides become the exception rather than the routine — is the arc's bet, judged by those counts, not a separately instrumented claim.

**Amendment (2026-07-31) — the phase 11 go/no-go reading: GO.** The phase 11 double sweep (deployment `gpt-5.4-2026-03-05`; both runs recorded in the phase 11 plan's amendment, run 1 on the committed scoreboard) moved the structure metrics beyond their bands in the bet's direction: JN1's connection F1 rose 0.5843 → 0.68 against a 0.043 band, with both runs above the entire four-sample historical range (0.5843–0.6436) and the mechanism exactly the arc's premise — edge recall 0.6923 → 0.8718 from map-only adjacencies the prose never stated; JN2's door recall rose 0.5882 → 0.8235 against a 0.0953 band from map-adopted doors; and JN2's map-proposed entrance corrected the positional heuristic's measured miss (run 2: 6/6, the run's single entrance dispute naming the correction). Door precision, the pre-registered uncertainty, landed benign (JN1 up, JN2 down within band); transitions stayed flat by construction. The premise — the model can read these maps well enough to trust with structure — held on adjacency, doors, and the entrance, so phase 12's footprint extension proceeds to planning. The contingency's residue, recorded honestly: the map read is itself run-to-run nondeterministic (minimod's one-edge module read successfully in one run of two), which the refreshed noise bands now carry, and the phase 12 plan should weigh that variance when it pins the footprint confidence bar.

## Companion workstream A — correction-loop usability

Standalone osr-forge PRs, no model spend, no phase machinery — each is small, independently reviewable, and shippable in any order. The measured frictions, from a walkthrough of the CLI surface and docs:

- **Workdir auto-discovery.** `convert my.pdf` writes `./my.forge`, then every follow-up command defaults `--workdir` to `.` — the single most repeated friction in the docs' own examples. Pinned remedy in the PR: the workdir-taking commands resolve a missing `--workdir` to the working directory when it is itself a workdir (today's supported default, kept), else to the unique `*.forge` directory within it; ambiguity and absence stay loud errors.
- **`osrforge report`.** A read-only summary command over `report.json`: validation status, flags grouped by area and kind, the monsters summary with unresolved names, findings by severity — the review loop's first step without opening JSON by hand. Machine consumers keep reading the artifact; this is presentation only.
- **A resume hint on stage failure.** The failed stage is already recorded in `run.json`; the CLI prints the exact `osrforge rerun <stage> --workdir <dir>` to type.
- **Sub-stage progress and running spend.** `on_progress` grows an additive event granularity below stage transitions (content batches, per-name stat-block requests) with running token usage, so a 45-minute content stage is not one silent line — and host apps (osr-editor's pipeline panel) inherit the stream.
- **A cost gate on `convert`.** The CLI runs the estimate first and asks before spending, with a `--yes` bypass; the library API is unchanged — hosts already own their own gates.
- **Preview legibility.** A coordinate ruler on the SVGs (the geometry override workflow asks humans to transcribe cell coordinates off a gridless image today) and a generated `previews/index.html` pairing each level's SVG with the module's own page renders. Byte-stability of the SVGs per settings stays a tested guarantee.
- **Docs correction.** `docs/getting-started/first-conversion.md` and `docs/reference/cli.md` claim the `estimate` workdir is warm for `convert`; it isn't — `convert` always re-renders (`preprocess` clears `pages/`). Fix the claim (or make it true; the PR pins which).

## Companion workstream B — osrlib upstream

Three additive extensions in osrlib-python, each shipped through that repo's own two-PR loop, motivated by downstream re-implementation — the strongest signal validation lives at the wrong layer:

- **Edge-key validation.** A malformed, non-canonical, or out-of-bounds `LevelSpec.edges` key is silently a wall today; osr-forge's `check` calls it "the single most dangerous silent failure in the loop" and re-implements the guard, and osr-editor mirrors it again. `validate_adventure` (and/or a `LevelSpec` validator — osrlib's call) learns to reject what it would silently ignore.
- **A structured findings API.** `validate_adventure` raises one newline-joined error string, which osr-editor parses back apart by enumerating the document — its own self-described contained fragility — and osr-web surfaces as a raw 422 at "Begin the adventure." osrlib already has the shape: `validate_content_pack` returns structured `PackFinding`s. An adventure counterpart returns findings with codes and addresses; the raising form remains for callers that want a gate.
- **`Adventure.to_document` / `from_document`.** `Character`, `Party`, and `ContentPack` have stamped-document surfaces; `Adventure` doesn't, so osr-forge mints the `"adventure"` kind by hand. Symmetry closes the gap.

After each ships, the downstream copies retire: osr-forge's `check` consumes the findings API and drops its own re-implementation of what osrlib now enforces — the `edge_invalid` entry in the report's enumerated finding vocabulary survives, fed by the findings API instead of forge's own scan, so no contract surface re-shapes — and osr-editor deletes its string parser. Retirement lands with the adopting PR, not the osrlib PR.

## Companion workstream C — consumer adoption

Recorded here as asks; specified and reviewed in each consumer's own repo:

- **osr-web** renders the feature descriptions it currently drops (`construction_trick` and `custom` features are dead text in the client), validates uploads with the findings API when workstream B lands (a broken conversion should fail at upload, not at "Begin the adventure"), and osr-forge's docs gain a "play your conversion" page for the already-working workdir drop-in route.
- **osr-editor** adopts the findings API in place of its validation-string parser, extends its mirrored flag vocabulary as phases 10–11 add badges, and closes its issue #39 (contents stranded on non-cache features — a conversion-fidelity hole its lint, osrlib validation, and forge's lint all miss today).

## Sequencing

Phases run strictly 9 → 10 → 11 → 12: the yardstick first, the cheap silent-failure kills second, the map third, shape last and contingent. Workstream A PRs interleave anywhere, with one exception: the preview-legibility PR (coordinate grid, side-by-side index) lands before phase 11 implementation, which assumes it as the human verification surface for map reconciliation. Workstream B lands before its adoption legs in workstream C and in forge's `check`. The regression rule binds every phase implementation exactly as `AGENTS.md` states it; BYOM entries refresh best-effort by module owners and never gate.

## Out of scope for this arc

Party and pregen extraction, non-PDF inputs, OCR fallback, and custom item and spell emission stay in `docs/spec.md`'s open-questions register with their recorded pickup conditions; per-commit CI evals stay forbidden by the spec's testing section, and a GUI in osr-forge stays a non-goal. Pixel-perfect map reproduction remains a non-goal even in phase 12: the map informs synthesis; the human confirms it.
