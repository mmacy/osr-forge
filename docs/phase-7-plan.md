# Phase 7 plan — custom monster emission

Implementation plan for phase 7 of [the osr-forge spec](spec.md): when a module's monster has no SRD template, emit the module's own — a real, playable `MonsterTemplate` extracted from the printed stat block — instead of forcing a flagged stand-in. This is the fix for the pipeline's deepest measured fidelity gap: DCC #81 scored **0.0 resolution accuracy** with every bespoke creature either substituted (`giant eyestalks → draco`) or implausibly matched (`primordial titan → normal_human`), and the committed corpus carries the same wound in miniature — JN1 has 9 unresolved names and JN2 has 16, mostly leveled NPCs and bespoke fauna *with printed stat blocks the pipeline reads and throws away*. The milestone: **a module whose monsters aren't in the SRD converts to a draft whose encounters carry the module's own creatures — validated by osrlib, spawnable in a real session, correctable through overrides — with emission exercised on the committed corpus and resolution measured on an extended metric instead of excluded from it.** The roadmap entry for this phase is a spec impact applied with the implementation PR, as before.

Five facts shape everything:

- **This is a two-repo phase, and the osrlib half is smaller than feared.** osrlib has no custom-catalog seam — five bare `load_monsters()` call sites sit upstream of spawn (validation, `GameSession.spawn`, the `SpawnMonsters` command, keyed encounters, listen checks) — but *downstream* of spawn everything travels free: `MonsterInstance` embeds its full template, so XP awards, treasure generation, combat stats, and persistence need zero changes. An `Adventure.monsters: tuple[MonsterTemplate, ...] = ()` field is layering-legal (crawl imports core; `adventure.py` already imports `MonsterCatalog`), rides saves and replay untouched, and needs no `SCHEMA_VERSION` bump under the stated additive rule. osrlib's own phase 6 pinned runtime content-injection out of scope "post-1.0 if a consumer ever demonstrates the need" — this is that demonstration, and document-carried data is not a runtime registration API. The API freeze is owner-waived (2026-07-16, recorded in the phase 6 plan), so the seam is designed outright.
- **Emission is extraction, not invention.** The spec's non-goal — "an unresolvable monster is never invented" — was aimed at the model conjuring stats from vibes. A template whose every field either comes off the printed page or is derived from printed values by the game's own rules tables (osrlib ships THAC0-by-HD, monster-saves-by-HD, and XP-by-HD exactly "for validation and custom monsters") is the opposite of invention, and every derived-not-printed field is flagged. The non-goal is rewritten, not repealed: nothing is ever conjured, and stand-ins remain the fallback for a name with no extractable stat block.
- **The printed stat block is visible to the pipeline today and cached nowhere.** The content prompt already tells the model encounters come "with stat blocks," then extracts only a name and a count. The three formats on the shelf span a clean difficulty gradient: OSE prints nearly the whole `MonsterTemplate` (dual AC `5 [14]`, THAC0 `[+2]`, D/W/P/B/S saves, ML, XP — near-direct mapping); TSR B/X prints AC/HD/hp/#AT/D/MV/ML/AL (THAC0, saves, and XP derive from HD); DCC prints an ascending-AC, Fort/Ref/Will block (AC converts by 19-complement, saves and morale derive from HD, flagged). B4's tabular stat table scrambles in the text layer — the stat-block pass sends page images for exactly this reason.
- **The eval seam already exists; it needs one more assertion.** `TruthEncounter.template` omitted means "no SRD template" — that's why DCC's resolution denominator collapsed to 1 (its 8 name-matched bespoke creatures are counted `non_srd` and excluded). The truth format needs a way to say "this monster should emit," and the metric needs to score it; both are small, and the committed corpus provides the measurement substrate (JN1+JN2's 25 unresolved names) without a byte of retail content.
- **The measurement machinery this phase rides is phase 6's, verbatim.** Prompt and schema changes strand fixtures (owner-run re-records; the classifier refuses agent-side live calls), the eval regression rule fires (double sweep, band refresh, first run committed as-is), the BYOM four re-convert and re-score, and changelog bullets land in the same PR. Phase 7 implements after phase 6's implementation merges, in phase order — the two phases' fixture re-records and sweeps stack, and this plan is written against main plus the merged phase 6 plan.

## Scope

In scope:

- The osrlib seam: adventure-bundled custom monster templates, resolved everywhere the engine resolves template ids — its consumer-facing contract pinned here, its internals owned by an osrlib phase (plan and implementation PRs in that repo, its own rubber-duck loops)
- The stat-block pass: a new model pass in the monsters stage extracting printed stat blocks for names the four resolution tiers left unresolved, cached raw
- Template mapping and emission: deterministic assembly of extracted stat blocks into `MonsterTemplate`s under a pinned per-format policy, every derived field flagged, bundled into the draft's `Adventure.monsters`
- The correction channel: a `monster_templates` override kind for patching or supplying stat-block fields per name
- The eval extension: a truth assertion for expected emission, resolution-metric semantics that score it, committed-corpus truth updates, and the JN1 baseline re-blessed
- The LLM resolution tier's null-hardening: with emission behind it, "none of these" becomes the safe answer, and the prompt leans into it
- The full measurement pass: fixture re-records, double sweep with band refresh, BYOM re-runs — owner-executed live work throughout

Out of scope, each with its disposition:

- **Custom item and spell emission** — the same story for `FeatureSpec.item_ids` and bespoke magic; deferred until a consumer or a measured gap demands it, and named so the deferral is visible. The osrlib seam this phase builds is the template to copy.
- **A dedicated NPC path for leveled humans** — many unresolved names are statted NPCs ("human cleric 3", "orc war leader"); emission models them as custom monsters, which plays correctly. Modeling them through osrlib's NPC-adventurer machinery instead is a design question for a phase with evidence it matters.
- **Eval name-matching drift** — exact-normalized matching keeps `giant acid worms` (extracted plural) from matching truth's `giant acid worm`, sinking encounters out of every downstream metric. A real confound, recorded with the DCC evidence, but touching match semantics mid-phase moves the yardstick; deferred deliberately.
- **A hallucination-guard metric for wrong-but-non-null LLM picks** — `primordial titan → normal_human` emits no flag today. This phase reduces the incentive (null-hardening) and the stakes (null now yields the real creature), but the metric remains the recorded phase 5 asymmetry, unpicked.
- **Promised extraction of tabular stat blocks** — the pass sends page images, so B4's table is *in reach*, but table extraction quality is measured, not promised; the amendment records what the table case actually did.
- **Custom monsters entering any osrlib or osr-forge repo data** — templates extracted from retail modules exist only in the user's workdir artifacts, exactly like every other conversion output; test fixtures draw only from the freely licensed corpus. Permanent fence, not a deferral.

Spec impacts, applied with the implementation PR:

- **§ Roadmap** gains entry 8: *Phase 7 — custom monster emission.* Adventure-bundled `MonsterTemplate`s extracted from printed stat blocks (an osrlib seam plus a stat-block pass, mapping policy, and override kind), with resolution measured on an extended metric. Milestone: a module's bespoke monsters convert to playable custom templates exercised on the committed corpus.
- **§ Non-goals**: "No custom game content" is rewritten — emission of a template whose every field is printed or rules-derived-and-flagged is extraction; invention stays banned; stand-ins remain the no-stat-block fallback. The parenthetical open-question pointer is dropped.
- **§ Pipeline stage 4 (monsters)** gains the stat-block pass; **stage 6 (assemble)** gains template mapping and bundling.
- **§ The contract / § Extraction report**: the monsters summary's custom-templates section; the flag vocabulary sentence gains `monster custom`.
- **§ Overrides**: the `monster_templates` kind joins the supported list.
- **§ Configuration / README knobs table**: the `custom_monsters` knob.
- **§ Testing and evals / docs evals page**: the extended resolution semantics and the custom assertion.
- **§ Open questions**: the custom-monster entry is removed (it is now the roadmap); the custom item/spell deferral is recorded in its place.
- **Docs site**: the correction-loop guide's monster section gains the template-patching workflow; `workdir-artifacts.md`, `settings-and-rerun.md`, `evals.md`, and the CLI's seeded `OVERRIDES_TEMPLATE` comment update; `tools/docs/gen_vocabulary.py`'s description dict gains the new flag and corrects `monster_unresolved`'s stale description (it omits the best-effort `→ stand-in` detail form today).

## Work items

### 1. The osrlib seam — bundled templates (contract here, internals in osrlib's own phase)

The consumer-facing contract osr-forge pins and the osrlib phase implements:

- `Adventure.monsters: tuple[MonsterTemplate, ...] = ()` — custom templates carried by the adventure document itself. Additive, defaulted: version-2 saves and every existing document load unchanged, no `SCHEMA_VERSION` bump (the stated additive rule), no migration entry, goldens byte-identical.
- **The effective catalog is base ∪ bundled, everywhere upstream of spawn.** `validate_adventure` validates bundled templates (they are full pydantic models, so shape is free) plus: bundled ids unique, no bundled id colliding with the base catalog — shadowing an SRD template would silently re-stat every reference to it, so collision is a validation error, never a merge — and every keyed-encounter reference and alignment pin resolves against the union. The five bare `load_monsters()` engine sites re-thread onto a session-held effective catalog; how (parameter, session field, module seam) is the osrlib plan's design space, but the observable contract is total: `GameSession.spawn`, `SpawnMonsters`, keyed encounters, and listen checks all resolve bundled ids.
- **Inline wandering-table ids join validation.** `WanderingSpec.table` rows carry `monster_ids` nothing validates today — a deferred play-time `ValueError` surface that predates this phase. The osrlib phase closes it against the effective catalog; recorded here because bundled monsters in wandering tables are exactly what a converted module will eventually want.
- Downstream of spawn, nothing changes by construction: `MonsterInstance` embeds its template, so combat, morale, XP awards (`template.xp`), treasure (`template.treasure`), persistence, and replay carry custom monsters with zero further work — the fact that makes this seam small.
- osrlib's docs gain the monster analogue of its authoring-custom-content guide section; its generated monsters-index continues to document the shipped catalog only. Versioning and release mechanics are osrlib's call under the waived freeze; osr-forge bumps its dependency pin to the released version in the same commit that consumes the seam.

### 2. The stat-block pass — `monsters.py`, `contracts/stages.py`

- A new model pass inside the monsters stage, gated by the new knob `custom_monsters: Literal["emit", "off"] = "emit"` (owned by the monsters stage in the knob map). It runs after the four resolution tiers, over exactly the names still unresolved: for each, one request carrying the pages where that name's encounters appear (`source_pages` from the content caches, deduplicated) as text *and images* — images because the one stat-block form the text layer destroys is the tabular one (B4's monster table scrambles columns on extraction; inline OSE/TSR/DCC blocks merely reflow).
- The extraction schema is the *printed* block, system-neutral, per name: AC as printed with its notation (descending, ascending, or dual), HD text, hp, attack lines with damage, movement text, saves as printed (D/W/P/B/S values, "save as F2", or Fort/Ref/Will), morale, alignment, XP if printed, number-appearing if printed, special-ability lines, and a self-assessed confidence with `source_pages`. The model transcribes; it converts nothing — every rules judgment lives in deterministic mapping code (work item 3) where it is testable and correctable.
- Output is a new cache, `stages/statblocks.json`: raw extracted blocks keyed by normalized name, `schema_version`-stamped like every stage cache. A name whose pages yield no stat block is cached as absent — the pass ran, found nothing, and assembly falls back to today's stand-in machinery; re-running assembly must not re-roll the model (purity).
- **Null-hardening rides along.** The resolution LLM tier's prompt currently begs the model never to pick a "close enough" substitute, yet DCC still produced `wall of eyes → warp_beast` and `library books → grey_ooze` — flagless, because a non-null pick is "resolved." With emission behind it, answering null now yields the module's own creature instead of a hash-picked stand-in, so the prompt is hardened to prefer null on doubt. Same stage, same re-record.
- Fixture impact: the monsters-stage fixtures re-record (new pass, hardened prompt); survey and content fixtures are untouched by this phase's schema — the stat-block pass reads existing caches and pages.

### 3. Template mapping and emission — `assemble.py`

Deterministic mapping from cached raw blocks to `MonsterTemplate`, run inside pure assembly so overrides apply first and corrections never re-roll the model:

- **The pinned anchors.** Descending↔ascending AC convert by 19-complement (the B/X identity OSE prints directly: `AC 5 [14]`). THAC0 and attack bonus: printed when printed (OSE brackets), else derived by osrlib's THAC0-by-HD table. Saves: printed D/W/P/B/S; "save as `F2`" through the class table; Fort/Ref/Will (DCC) discarded and derived from HD. XP: printed, else osrlib's XP-by-HD table with one asterisk per extracted special-ability line (the B/X rule). Morale: printed, else 8 — dead center of the 2–12 scale. Alignment: the printed letter as a single-option `AlignmentSpec`; unparseable → neutral. Number appearing: printed, else fixed at the keyed count. Movement: printed modes onto `MovementMode`; a lone rate is the turn rate with the encounter rate its third. Treasure: `TreasureRef()` empty — keyed treasure is already the area's, and inventing a treasure type would be invention. Attack lines parse into `AttackRoutine`s under the existing dice grammar (range notation `1-10` → `1d10`); a block with no parseable attack maps with `attacks=()` — legal, and flagged like every other gap.
- **Every field that is derived, defaulted, or discarded-and-rederived is flagged** — one flag per affected area, `monster_custom:<name>`, with the report's monsters summary carrying the full record: per emitted template, its id, source name, `source_pages`, and the list of derived-not-printed fields. The flag is the review badge; the summary is the review detail. `monster_custom` joins the `Flag` enum, the spec's vocabulary sentence, and the generated badge table.
- **Ids**: `slugify(name)` under the catalog's `^[a-z][a-z0-9_]*$` convention; a collision with the base catalog or a sibling emission appends a numeric suffix deterministically. Resolution for the name becomes `method="custom"` with the emitted id — the cache validator's "template id exactly when resolved" rule holds unchanged, and `MonsterSummary.resolved` counts it honestly.
- Finer per-format judgment than the anchors above (DCC action dice, movement descriptors, multi-form hp lists) is implementer latitude inside one non-negotiable rule: a mapped value is either traceably printed or flagged as derived — never silently guessed.
- `unresolved_fallback` keeps its exact meaning for the residue: a name with no extracted stat block still gets the stand-in (`best-effort`) or the gap (`omit`), flagged `monster_unresolved` as today. Under `custom_monsters: off`, assembly ignores the stat-block cache entirely and phase 6 behavior is bit-for-bit preserved.
- osr-forge's own validation call threads the union: `validate_adventure(adventure, <base ∪ bundled>, load_equipment())` per the seam's contract, so a draft with emitted templates passes the gate it fails today.

### 4. The correction channel — `contracts/overrides.py`, `overrides.py`

- A new override kind, `monster_templates:`, keyed by normalized monster name like the existing `monsters:` remap: each entry patches fields of the extracted raw block (pre-mapping — so one correction fixes AC once, not AC-descending and AC-ascending twice) or supplies a complete block for a name the pass missed, enabling emission for it. Every entry carries a `reason`; every entry must take effect or assembly fails loudly; duplicate keys are rejected — the three standing override rules, unchanged.
- The two monster kinds compose predictably: a `monsters:` remap for a name wins over emission (the human said "use this catalog id"), and remapping *to* an emitted id is legal — the id is in the union like any other. The correction-loop guide's monster section documents the decision tree: wrong SRD pick → remap; bad extracted field → template patch; no stat block found but one is printed → supply the block.

### 5. The eval extension — `evals.py`, corpus truth

- `TruthEncounter` gains `custom: bool = False`, legal only when `template` is omitted: `template: <id>` asserts the SRD resolution as today; omitted-with-`custom: true` asserts *this creature should emit*; omitted-without stays exactly today's `non_srd` (no assertion about emission — partial truth stays honest). The resolution family scores custom-asserted encounters into the denominator, matched when the cache's method is `custom` (emitted ids are pipeline-internal, so method — not id equality — is the assertion), and `EncounterMetrics` grows `custom_denominator`/`custom_matched` beside the existing fields so the scoreboard shows emission as its own legible number rather than diluting SRD-resolution accuracy.
- The committed corpus truths update where the printed page supports the assertion — JN1's and JN2's unresolved names (9 and 16) are the population, and each `custom: true` is an authored judgment from the printed stat block under the same independence discipline as every other truth fact. The JN1 pinned baseline re-blesses with the new fields (recomputed from committed caches; old caches carry no `custom` methods, so the pre-sweep numbers move only by field addition).
- BYOM truths are the owner's, offline: adding `custom: true` to DCC's eight bespoke creatures is the natural edit, `publish`'s truth-hash refusal makes re-scoring the designed remedy, and the deferred-publication posture is untouched.
- The scorer's other families are untouched, and the name-matching confound stays recorded, not fixed — the yardstick moves in exactly one dimension this phase.

### 6. Measurement, fixtures, and the standing obligations

- **Owner-run live work**: monsters-stage fixture re-records (new pass + hardened prompt); the double corpus sweep; BYOM re-conversion and re-scoring of the four (B3 with `--set 'blank_page_renders=[21]'`). The phase 5 permission note stands.
- **The regression rule fires**: first sweep run committed as-is, band refreshed in `tools/eval/README.md`, out-of-band moves justified in the PR description. The sweep is also the emission acceptance evidence on free content: JN1/JN2's custom-asserted encounters score on the new metric.
- **DCC #81 is the milestone's measured witness**: re-run and re-scored, its resolution row moves from `denominator 1, accuracy 0.0, non_srd 8` to custom-asserted encounters scored on the extended metric; the before/after lands in the amendment. HotO and B3/B4 re-run alongside; B4's tabular-monster pages are the recorded table-extraction probe.
- **Consumer acceptance, recorded not gated**: a re-converted DCC draft loads in osr-web and a bespoke creature is fought in a live session — the seam proven end to end through an unmodified consumer.
- **Changelog bullets** in the same PR; cost envelope ≈ $10–12 at measured rates (the stat-block pass adds roughly one small request per unresolved name), ≤ $15 for the phase.

### 7. Tests

- osrlib (in its repo, per its phase): union validation (collision, dangling, alignment pins, wandering ids), spawn-through-session of a bundled template, persistence round-trip of `Adventure.monsters`, goldens byte-identical.
- Stat-block pass: request planning (pages per name, dedup), cache round-trip, absent-block caching, `custom_monsters: off` short-circuit; all against `FixtureProvider`, no network.
- Mapping: table-driven per-format cases pinning every anchor (19-complement, THAC0/saves/XP derivation, morale default, asterisk rule, `1-10 → 1d10`, lone-rate movement, no-attack blocks) and the derived-field flag record; id slugging and collision suffixing; emitted drafts pass `validate_adventure` with the union and fail without it.
- Overrides: template patches apply pre-mapping and must take effect; supplying a block enables emission; remap-over-emission precedence; remap-to-emitted-id resolves.
- Evals: `custom` assertion round-trips; custom-asserted encounters enter the custom denominator and match on method; omitted-without-custom stays `non_srd`; the JN1 baseline re-blessed with the new fields.
- Assembly purity: emission is a pure function of caches plus overrides — byte-stable artifacts, no model call on re-assembly.
- All green under the full gate on both OSes, no network anywhere.

## Sequencing

1. The osrlib phase, end to end in that repo (plan PR → implementation PR → release); osr-forge bumps its pin. Nothing in osr-forge merges against an unreleased seam.
2. The stat-block pass and its cache, the null-hardened resolution prompt, and the owner-run fixture re-records (work item 2).
3. Mapping, emission, flags, the report's custom section, and the override kind (work items 3–4) — deterministic work, fully testable offline against the re-recorded fixtures.
4. The eval extension and committed-corpus truth updates, JN1 re-blessed (work item 5).
5. The measurement pass: double sweep, band refresh, BYOM re-runs, the osr-web live-session witness; spec, README, and docs impacts; the amendment; the traceability pass.

Phase order holds throughout: phase 7 implementation starts after phase 6 implementation merges, and this plan is amended on the phase-7-impl branch if phase 6's implementation moves anything it leans on.

## Definition of done

- Both repos green: osrlib's full gate with the seam released and pinned; osr-forge's `uv sync && uv run ruff format --check && uv run ruff check && uv run pyright && uv run pytest` on both OSes, no network in any test, `mkdocs build --strict` clean.
- A bundled custom template is validated, spawned, fought, persisted, and replayed through the released osrlib — pinned by osrlib's tests and osr-forge's compatibility fixtures.
- The stat-block pass caches raw printed blocks for unresolved names; mapping emits templates under the pinned anchors with every derived field flagged and recorded in the monsters summary; `custom_monsters: off` reproduces phase 6 behavior exactly; stand-ins remain for the no-block residue — each behavior pinned by the tests above.
- `monster_templates:` overrides patch or supply blocks under the three standing override rules, with precedence over emission pinned.
- The truth format's `custom` assertion is scored as its own metric pair; JN1/JN2 truths carry the assertions their printed pages support; the JN1 baseline is re-blessed.
- Fixtures stranded by the monsters-stage changes are re-recorded; the double sweep is committed (first run as-is) with the refreshed band; out-of-band moves are justified; changelog bullets land in the same PR.
- The BYOM four are re-run and re-scored against owner-updated truths, DCC #81's resolution before/after in the amendment; the osr-web live-session witness is recorded; publication posture unchanged from phase 5.
- The spec impacts above — roadmap entry 8, the non-goal rewrite, stage 4/6 wording, contract and overrides additions, the knob, the evals rewrite, the open-questions swap — and every README/docs surface named are applied with the implementation PR; every item above is traceable to code and tests or to a named amendment entry.
