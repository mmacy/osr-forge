# Phase 10 plan — resolution and survey hardening

Implementation plan for phase 10 of [the osr-forge spec](spec.md), the second phase of [the improvement arc](improvement-spec.md): kill the silent failure modes that don't need the map. A deterministic printed-stat veto on non-exact monster resolutions, a survey self-consistency census, and confidence thresholding — every new inference flagged, one implementation PR, one fixture re-record, one live double sweep. The milestone: **a wrong resolution can no longer be silent — every non-exact pick is either stat-confirmed or flagged — and a mode-flipped survey flags itself in the run it happens in**, measured on the committed corpus with the phase 9 families.

Six facts shape everything — the first two reframe the arc's own claim:

- **The flagless wrong pick lives entirely in the custom population, and the veto is a bullseye on it.** Measured per-encounter against truth on the retained phase 7 pair: JN1's custom misses are ten bespoke leaders LLM-resolved onto their base monster (`orc chief` → `orc`, `goblin king` → `goblin`, the whole chieftain family), and JN2 adds six LLM picks plus **one fuzzy-tier accept** (`giant eel` → `weasel_giant` at ≥ 0.85) — direct evidence the fuzzy tier needs gating too. Every one has a printed block one HD count away from its template, and `usable_stat_block` is already the scorer's own custom-match signal, so a vetoed name with a usable block scores as a custom match immediately.
- **The veto cannot raise resolution accuracy on this corpus — it can only protect or spend it.** Every resolution-accuracy miss in both sweeps is a false *null* (`fighters` → `veteran_2` and kin answered `unresolved`), zero are wrong picks. Meanwhile ten-plus truth-confirmed non-exact picks (`giant fire beetle` → `fire_beetle`, `python` → `rock_python`, `yellow mold` → `yellow_mould`…) are the false-veto population the tolerance must protect. The arc spec's claim that the veto "attacks custom accuracy, resolution accuracy, and the flagless wrong pick with one mechanism" is measurably half right; the improvement spec takes a one-line amendment with the implementation PR, and the plan pins the tolerance to the evidence.
- **Confidence thresholding is an honest uncertainty badge, not a hallucination guard.** JN2's eight unmatched extracted areas carry confidences 0.84–0.99 — *above* the workdir median — and aren't hallucinations at all (sub-lettered features the truth deliberately scopes out). The distributions over all ten retained workdirs (770 areas): nothing below 0.5 anywhere (a 0.5 floor is a no-op), 0.6 fires six times corpus-wide (a genuine rarity badge), 0.7 fires 44 times and sits on the steep part of between-sweep instability (JN1's under-0.6 count moves 5 → 0 across sweeps). The floor pins at **0.6**.
- **The comparator's parsers exist and are total — but live in the wrong module.** `assemble.py` owns `_parse_ac` (dual `5 [14]`, single-value 19-complement with a `complement_derived` hazard flag), `_parse_hd_text` (`3+1`, `1-1`, `½`, asterisks), and `_parse_class_level`; `assemble.py` imports `monsters.py`, so the veto in the monsters stage cannot import them without a cycle. They move to a shared module below both — the honest option, because the veto must change the *cached* resolution or every downstream consumer disagrees with the cache.
- **The stat-block pass is coverage-agnostic and its extension is one line.** The page planner takes only a name, the content caches, and page texts; nothing downstream knows how a name resolved. Extending the pass to LLM- and fuzzy-resolved names is a population change at one line, existing statblock fixtures survive (fingerprints are per-request), and minimod — whose five names all resolve exact — gains zero requests, so its fixture directory and goldens' `statblocks.json` are untouched by the coverage change.
- **The census's cost lever is images, and blindness is the wrong trade.** A survey-shaped census costs ≈ $0.21–0.31 per module (input dominated by page images), a text-only census roughly half — but a text-only census is blind on scanned modules, which are exactly where the mode-flip risk concentrates. The census sends full page parts, mirrors the survey's chunk-and-merge path verbatim when the source exceeds the window, and costs what faithfulness costs.

## Scope

In scope:

- **The shared stat-block parsing module.** `_parse_ac`, `_parse_hd_text`, `_parse_class_level`, and `_ParsedHd` move from `assemble.py` to a new `src/osrforge/statblocks.py` (public names), imported by both assembly and the monsters stage. Pure movement plus visibility — behavior byte-identical, no aliases left behind.
- **Stat-aware resolution.** The stat-block pass's population widens from unresolved names to unresolved ∪ LLM-resolved ∪ fuzzy-resolved. After the pass, a deterministic comparator runs per LLM/fuzzy-resolved name: veto — the pick resolves to null and flows into the existing custom-emission path — exactly when (a) the printed block is usable (`usable_stat_block`), (b) both the printed HD and the template HD parse structurally, and (c) the HD *count* differs by ≥ 1 (`MonsterHitDice.count` vs the parsed count; class-level blocks compare level to count the way assembly's mapping already does). AC never vetoes — single-value AC direction rests on a defaulted assumption (`complement_derived`), and a false veto spends resolution accuracy the corpus shows we don't have to spend. The vetoed pick is preserved on an additive defaulted `MonsterResolution` field (the cache invariant `template_id is None ⟺ method == "unresolved"` holds; the veto record rides beside it), and the report's monsters summary surfaces it.
- **The `resolution_suspect` flag** (additive vocabulary): emitted at assembly for (i) an HD-modifier-only difference on a surviving non-exact pick, (ii) an AC mismatch on a surviving non-exact pick, and (iii) a comparison that rests on a derived AC complement and disagrees. Detail names both readings (`resolution_suspect:orc war leader → orc, printed HD 2 vs 1`). A surviving pick with no usable printed block stays unflagged — no evidence, no badge.
- **The survey census.** A second, reduced request after the survey: a distinct short system prompt and a strict projection of the survey schema — dungeons, levels, and per-level printed-key *ranges* only (no per-area records, no town, no monster names). Same page parts, same chunk-and-merge machinery over the same windows when chunked. A deterministic comparison of census vs survey site sets over `canonical_slug` (the merge machinery's own join key); any disagreement — a site in one and not the other, or level counts differing for a matched site — lands as an additive defaulted `SurveyIndex` field naming the disagreements, which assembly turns into a module-scope flag `survey_disputed:<detail>` (additive vocabulary). Flag-only in v1: a re-roll policy is deferred until a measured recurrence, recorded here.
- **Confidence thresholding.** Assembly emits `low_confidence:<value> self-assessed` on areas whose cached confidence is below 0.6 — deterministic, no schema change, reads the existing cached value. Framed in docs as the model's own uncertainty made visible, explicitly *not* a hallucination guard.
- **`estimate` learns both costs**: a census term (survey-shaped input, small output) and a widened monsters term (the flat 5K-token constant models only the LLM tier and is an order of magnitude low for `emit` runs with the widened pass).
- **The measurement pass**: fixture re-records (the census fixture for minimod's replay chain; the JN1 excerpt-chain directory assertion checked), golden re-blesses (additive contract fields serialize), the live double sweep with the veto and census in play, both scoreboards' committed halves updated, band-checked with justifications where movement exceeds bands.

Out of scope, each with its disposition:

- **Raising resolution accuracy.** The corpus's resolution misses are false nulls the veto cannot reach (they need alias-table or prompt work that would strand fixtures for a different goal); the phase's resolution obligation is *protection* — the false-veto population survives — with the one knowingly-spent case below.
- **A re-roll on census disagreement.** Flag-only; the disagreement's frequency is unmeasured, and a re-roll policy designed against zero observations is speculation. Deferred to the first phase that observes a disputed survey in the wild.
- **AC-based vetoes and exact/alias-tier gating.** AC only flags (the direction hazard); exact and alias tiers stay ungated — an exact name match contradicted by its stat block is a module re-statting a catalog monster, which is the module's prerogative and the human's call, and gating it would add fixture cost (minimod's five exact names) for zero measured benefit.
- **The `giant crab offspring` class of truth disagreements.** The veto will null JN2's `giant crab offspring` → `crab_giant` (printed HD 1 vs 3) and JN1's `young lizard men` → `lizard_man` (Δ1) — truth-confirmed picks whose printed blocks are genuinely different creatures, both already filed as arguable rank-variant assertions in the frozen-key findings (#30). Expected movement: JN2 resolution accuracy −0.037 (22/27), *outside* the 0.02 band — the PR carries the justification (the draft emits the module's own printed creature, the spec's stated preference; the metric change is the cost of truth's arguable template assertions, not a quality loss), and custom accuracy's expected rise (JN1 toward ~0.9 if the chieftain blocks prove usable) is the offsetting evidence.
- **Wandering-monster and NPC-party extraction** — unchanged, out of the arc entirely.

Spec impacts, applied with the implementation PR:

- **`docs/spec.md`** § Pipeline stage 2 (survey) gains the census sentence; stage 4 (monsters) gains the widened pass and the veto; § Extraction report's flag sentence gains `resolution_suspect` and `survey_disputed`; § Configuration notes the census inside the survey stage's cost.
- **`docs/improvement-spec.md`** phase 10 section: the one-line amendment correcting "attacks custom accuracy, resolution accuracy, and the flagless wrong pick" to the measured framing (custom-accuracy mechanism; resolution protected, not attacked).

## Work items

### 1. `statblocks.py` — the shared parsing module

Move `_parse_ac` → `parse_ac`, `_parse_hd_text` → `parse_hd_text`, `_parse_class_level` → `parse_class_level`, `_ParsedHd` → `ParsedHd` into `src/osrforge/statblocks.py`; `assemble.py` imports them; no re-exports remain in `assemble.py`. `usable_stat_block` stays in `assemble.py` (it is assembly's refusal-ladder predicate and the scorer imports it from there; moving it would churn two consumers for no gain — recorded as deliberate).

### 2. The veto — `monsters.py`, `contracts/stages.py`

- The stat-block population at the one-line site becomes unresolved ∪ `llm` ∪ `fuzzy`.
- `MonsterResolution` gains additive defaulted fields: `vetoed_template_id: str | None = None` and `veto_detail: str | None = None` (the human-readable both-readings string). Validator addition: `vetoed_template_id` is legal only with `method == "unresolved"`.
- The comparator (new, in `monsters.py`, importing `statblocks.py` and reading the already-cached template catalog): per `llm`/`fuzzy` resolution with a usable block, parse printed HD (or class level → count), compare to the template's `MonsterHitDice.count`; Δ ≥ 1 → flip the cached entry to `unresolved` carrying the veto fields. Runs inside the monsters stage after the stat-block loop, before the cache writes — the cache is the record.
- Suspect detection is *assembly's* job (work item 4): the comparator caches facts; assembly judges survivors, keeping all rules judgment in deterministic assembly per the stage's own design line.

### 3. The census — `survey.py`, `contracts/stages.py`

- `CENSUS_SYSTEM` (short, site-discipline-focused — it restates the survey prompt's own site rules: separate lairs with own maps are separate dungeons) and `CENSUS_SCHEMA` (dungeons → name + levels → number + first/last printed key). `build_census_request` / `build_chunked_census_request`, tag `census`, mirroring the survey builders; the chunked path merges census answers with the same first-occurrence discipline before comparison.
- The comparison (pure function, unit-tested): census site-slug set vs survey site-slug set; per-site level-count agreement. Output lands on `SurveyIndex.census_disputes: tuple[str, ...] = ()` (additive, defaulted — the `TownInfo.services` precedent), each entry a stable human-readable disagreement (`census names 'crypt of horrors'; survey does not`).
- The census runs inside `survey()`'s `track_stage` after normalization, before the cache write. Census usage folds into the survey stage's `run.json` usage block (no new stage — the census is the survey checking itself, and `rerun survey` re-runs both).

### 4. Assembly and the report — `assemble.py`, `contracts/report.py`

- `Flag` gains `RESOLUTION_SUSPECT` and `SURVEY_DISPUTED` (additive vocabulary; descriptions in the vocabulary generator).
- Assembly emits: `survey_disputed` per `SurveyIndex.census_disputes` entry (module-scope); `resolution_suspect` per surviving non-exact pick meeting the (i)–(iii) conditions, on each area whose encounter carries the name; `low_confidence:<value> self-assessed` on areas below the 0.6 floor. The monsters summary's unresolved records surface `vetoed_template_id` (additive report field on the summary's entries — osr-editor renders new report metadata without schema work).
- The `0.6` floor is a named constant with the distribution evidence in its docstring, deliberately not a settings knob: a knob invites tuning what should be re-pinned against data, and the arc's flags are contracts, not preferences. (Revisit recorded if a corpus member's distribution shifts.)

### 5. `estimate.py`

The census term (page-image-dominated input identical to the survey's, output flat ~500), and the monsters term widened to model the stat-block pass: requests ≈ distinct non-exact names (the run-time population is unknowable pre-survey; the estimate documents its heuristic — names cannot be counted before extraction, so the term prices from page count the way the content term does, with the JN1/JN2 measured means as constants). Estimate remains a rough gate, not a promise; its docstring says so already.

### 6. Tests and fixtures

- Unit tests: the comparator's veto/protect/flag matrix over synthetic blocks and templates (chieftain Δ1 vetoes; `fire_beetle`-style same-count survives; modifier-only → suspect; derived-complement AC disagreement → suspect; unusable block → untouched); census comparison agreements and each disagreement shape; the confidence floor's boundary; `MonsterResolution` validator additions.
- Fixture work: record the minimod census fixture (one request; the replay module's five-name monsters stage is untouched); check `test_replay_directory_holds_exactly_the_chain_fixtures` for the JN1 excerpt chain; re-bless goldens (additive fields serialize into `survey.json`/`monsters.json` expected outputs).
- Pipeline tests: the census-disputed path and the veto path each exercised through `FixtureProvider` recordings with synthetic fixtures.

### 7. The measurement pass and the standing obligations

- The double sweep over the three committed members with veto + census live (≈ $6 including the veto's added stat-block requests), first run to the scoreboard, pair to the amendment, bands refreshed. Expected movements pinned above (custom up, JN2 resolution −0.037 justified); anything else outside a band gets its own justification or a revert.
- BYOM refresh stays owner-owned and best-effort per the standing rule; the phase does not run it (the four members' entries carry their `osrforge_version` stamps).
- Changelog bullet; the docs from the spec-impacts list; `docs/guides/correction-loop.md` gains the veto and suspect flags in its monster decision tree.

## Sequencing

1 (the module move, byte-identical, gate green) → 2 + 3 in either order (independent surfaces) → 4 (consumes both caches) → 5 + 6 together (fixtures record against the frozen prompts/schemas from 2–3) → 7 last (the sweep measures the finished behavior). One implementation PR after this plan merges; the fixture re-record and sweep land in it per the regression rule.

## Definition of done

- The veto and census are live behind no knobs, every new inference carries its flag, and the vetoed-pick record rides the cache and the report.
- `statblocks.py` exists with no aliases left in `assemble.py`; the gate is green; goldens re-blessed; the minimod census fixture recorded and the replay suite green at zero network.
- The double sweep is committed with the pair in the amendment, bands refreshed, and the JN2 resolution movement justified in the PR description.
- Spec impacts and the improvement-spec amendment applied; changelog bullet present.

## Amendments

(Recorded during implementation.)
