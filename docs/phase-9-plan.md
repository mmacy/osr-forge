# Phase 9 plan — measurement extension

Implementation plan for phase 9 of [the osr-forge spec](spec.md), the first phase of [the improvement arc](improvement-spec.md): the arc's yardstick, built before any extraction change ships. Three new metric families — doors, vertical transitions, the entrance — plus an encounter-precision metric riding a new asserted-empty truth convention, with the truth passes completed in-phase over every corpus member, the standing phase 7 sweep pair re-scored offline, the band table refreshed, and a contingent non-BFRPG committed member if a candidate passes the license procedure. The milestone: **door, transition, entrance, and encounter-precision quality are measured numbers on every corpus member — with noise bands on the committed corpus — before phases 10–12 touch a prompt.** The roadmap entry is already in the spec (improvement-arc PR #27); remaining spec impacts land with the implementation PR, as before.

Six facts shape everything:

- **The offline re-score works today, verified.** The retained phase 7 workdirs live at `~/osr-forge-measurement/phase7/` (a path recorded nowhere until now — this plan pins it): `sweep1-`/`sweep2-` pairs for all three committed members plus four BYOM workdirs, every one holding `run.json` and a complete `stages/` set. Re-scoring `sweep1-jn1-chaotic-caves.forge` against today's truth reproduces the committed scoreboard entry exactly (F1 0.6436, custom 0.2857, all four families) — so the existing families are a regression check on the scorer refactor: after this phase's changes, the re-score must reproduce them byte-for-byte, and any movement is a bug, not noise.
- **A cache-based door metric scores extraction, not geometry — and that seam is deliberate.** `score_workdir` reads stage caches only; geometry is synthesized in-memory at assembly and never cached. So the door family measures the *content pass's* door assertions (`via`, `door_locked` on `AreaConnection`), and geometry-only behaviors — the doors dropped on cycle-closing routes that phase 11 rescues — are invisible to it. The design answer is a seam, not a doctrine break: all edge facts (presence, via-class, door kind, locked) flow through one function over the caches, and phase 11 reroutes that function through its deterministic reconciliation — the phase 7 precedent (`usable_stat_block` imported into the scorer so the metric can never disagree with assembly's own predicate) applied to edges. Phase 9 pins the metric semantics; phase 11 swaps the fact source.
- **Transitions must be dungeon-scoped, not level-scoped.** The retained caches show three vertical wire shapes: a cross-level keyed link whose target key lives in a sibling level's cache (JN1 `roslof-manor` 85 ↔ 98), a level-shaped stub (`to_key: null, to_level: N`) that today's connection scorer explicitly skips, and — the shape that breaks any level-scoped design — a printed multi-level site the survey collapsed into one extracted level, so the printed inter-level stair reads as a *same-level* connection (JN2's Reed Marsh Temple: truth splits keys 1–8 / 9–14 across two levels; extraction has one level and `3 → 9, via: stairs, direction: up`). The metric therefore matches truth `(level, key) → (level, key)` endpoint pairs against extracted endpoints resolved through the dungeon-wide slug map, regardless of which level cache a connection came from. The measured stakes are real: JN2's sweep 1 has 27 stairs connections, zero with `to_level`, and an `adventure.json` with zero transitions across all six dungeons.
- **The entrance is derivable from `survey.json` alone.** Today's entrance is a pure positional heuristic — the first listed area of the lowest-numbered non-empty level (`geometry.py`) — reproducible in the scorer in a few lines with no content cache read. Truth supplies the printed answer; the metric scores the heuristic now (the pre-change baseline phase 11 needs) and the map-proposed entrance later through the same seam.
- **The asserted-empty flip is a live migration hazard.** `TruthArea.encounters` defaults to `()`, so an omitted key and `encounters: []` are indistinguishable today. Making the field `| None = None` silently flips every omitted key — 57 of JN1's 137 areas, 47 of JN2's 86, 1 of minimod's 6 — from "asserted none" to "unasserted." The committed truth passes therefore write explicit `encounters: []` on every verified-empty area (that verification *is* the pass), the full-assertion repo test extends to encounters, and the name-recall denominator keeps its meaning (listed truth encounters) by construction — JN1's 109 and JN2's 57 must not move for a non-quality reason.
- **The BYOM inventory is four scoreable members, not five.** `hole-in-the-oak`, `b3-palace-of-the-silver-princess`, `b4-the-lost-city`, and `dcc-81-the-one-who-watches-from-below` have retained phase 7 workdirs and scored entries; `aa1-adventure-anthology-one` has neither (withdrawn by the owner, never publishes). AA1 is excluded from this phase's truth passes — a pass over a member that can produce no number is ceremony — and the exclusion is recorded here rather than discovered mid-phase.

## Scope

In scope:

- The truth-schema extension: `doors` and assertion-aware `encounters` on `TruthArea`, `transitions` and `entrance` on `TruthDungeon`, with validators that keep contradictions unrepresentable
- The scorer extension: the edge-fact seam, the door family, the dungeon-scoped transition family, the entrance family, encounter precision — plus the metric models, `corpus_means`, and `_print_metrics` plumbing
- The truth passes over every scoreable member — committed (JN1, JN2, minimod) and BYOM (the four above) — authored and adversarially verified under `AUTHORING.md`, provenance appended, the owner-sampling delta filed as a GitHub issue
- The offline re-score of the standing pair, the refreshed band table with the new families' rows, the scoreboard regeneration, and the phase amendment recording the pair
- The contingent non-BFRPG committed member: candidate search under the phase 0 license procedure; on a pass, manifest + full truth + its own live double sweep (this phase's only live spend); on failure, the BF1-style record
- Docs: the README band table and conventions, `AUTHORING.md`'s new-family rules and the resolved asymmetry paragraph, `docs/evals.md`, glossary entries, the spec's family sentence, the changelog bullet

Out of scope, each with its disposition:

- **Stuck-door assertion.** JN1's caches record zero stuck doors and B3/B4/HotO none either — the denominator is empty outside JN2 and minimod. `TruthDoor` carries `kind` and `locked` only; stuck stays extractable (the cache field exists) and unmeasured until a member carries signal. Recorded, revisitable.
- **Direction-sense scoring on transitions.** Stairs are reciprocal and trapdoors/chutes one-way, but v1 matches undirected endpoint pairs plus kind; scoring the travel sense adds a truth convention (which end is "from") for near-zero discrimination on this corpus. Deferred with the kind vocabulary already in place.
- **A door-flag-on-non-door-via defect metric.** The caches show door conditions on non-door `via` values; geometry's posture is discard-with-flag, and the scorer mirrors it (such flags contribute no door fact). Counting them as a defect class is a diagnostic, not a quality metric; unpicked.
- **Geometry-realization measurement.** Whether a cached door fact survives into `adventure.json` edges is phase 11's territory via the seam; this phase scores extraction only. Named so nobody reads the door numbers as map fidelity.
- **AA1.** Excluded as above.
- **Any extraction prompt or schema change.** The point of the phase. The fixture-re-record and live-sweep obligations do not fire (the contingent member's sweep is corpus growth, not an extraction change).

Spec impacts, applied with the implementation PR:

- **§ Testing and evals**: "four metric families" becomes the extended enumeration (areas, encounters — recall, count, resolution, custom, precision — connections, treasure, doors, transitions, entrance), with the asserted-empty encounters convention noted alongside the existing assertion-aware families.
- **§ Open questions**: if the contingent member joins, the corpus-diversity entry records it; if not, the entry gains the failure record pointer.

## Work items

### 1. The truth schema — `evals.py` models, validators, repo tests

- `TruthArea.encounters: tuple[TruthEncounter, ...] | None = None` — present asserts the complete encounter list (possibly empty), omitted asserts nothing. The committed full-assertion test (`test_committed_members_stay_fully_pinned_and_asserted`) extends: every committed area asserts encounters.
- `TruthDoor` (new): `kind: Literal["door", "secret_door"]`, `locked: bool = False`. `TruthArea.doors: dict[str, TruthDoor] | None = None`, keyed by neighbor printed key — present asserts the complete door-fact set over that area's asserted connections; omitted asserts nothing. Validators: `doors` requires `connections` asserted on the same area, and every `doors` key must appear in `connections` (slug-matched). A `TruthLevel` validator rejects contradictory assertions on the same undirected pair (both endpoints may assert; they must agree).
- `TruthTransition` (new): `from_level: int`, `from_key: str`, `to_level: int`, `to_key: str`, `kind: Literal["stairs", "trapdoor", "chute"]` — the kind vocabulary mirrors geometry's `_transition_via` narrowing. `TruthDungeon.transitions: tuple[TruthTransition, ...] | None = None` — present asserts the dungeon's complete vertical-link set; each endpoint's level must exist and its key must slug-match an area on that truth level (validator). One entry per link, endpoint order free (matching is undirected).
- `TruthEntrance` (new): `level: int`, `key: str` (same existence validators). `TruthDungeon.entrance: TruthEntrance | None = None`.
- The committed-corpus assertion posture, pinned: every committed area asserts `encounters` and (as today) `treasure`; `doors` asserted on every area whose `connections` are asserted; `transitions` and `entrance` asserted on every committed dungeon. Partial assertion stays the BYOM posture. The same-level rule for `connections` is untouched — vertical links live only in `transitions`, and `test_truth_connections_reference_same_level_keys` stands.

### 2. The scorer — the edge-fact seam and four new computations

- **The seam.** One function derives per-dungeon undirected edge facts from the caches — endpoints (matched slugs), via-class, door kind, locked — and both the existing connection F1 and the new door family consume it. Dedup rule for the two directed mentions of one edge, pinned: door presence if either side states a door via; kind conflict resolves to `secret_door` (the more specific claim); locked if either side states it; door conditions on non-door via contribute nothing (geometry's discard posture). The connection F1's semantics are byte-identical through this refactor — the standing-pair re-score proves it.
- **Doors** (`DoorMetrics`): universe = undirected edges between matched areas where at least one endpoint asserts `doors`, restricted (like connections) to edges with an asserted-connections endpoint. Counts: `truth_doors`, `extracted_doors`, `true_positives`, `recall`, `precision`; on true positives, `kind_matched`/`kind_accuracy` and `locked_matched`/`locked_accuracy`. House style throughout: counts first, `_ratio` rounding, `None` on empty denominators.
- **Transitions** (`TransitionMetrics`): a per-dungeon pass after the level loop, over a dungeon-wide `slug → truth level` map. An extracted vertical mention is a connection with (`to_level` set) or (via ∈ {stairs, trapdoor, chute} and direction ∈ {up, down}) or (via vertical and the target key resolving to a *different* truth level of the same dungeon — the collapsed-level and cross-level shapes). Truth side: asserted `transitions` on aligned dungeons. Matching is undirected endpoint pairs; a level-shaped mention (`to_key: null, to_level: N`) matches on from-endpoint plus target level alone — the landing key is geometry's guess policy, not extraction's claim, and the metric scores extraction. Counts: `asserted_dungeons`, `truth_transitions`, `extracted_transitions`, `true_positives`, `recall`, `precision`, `kind_matched`/`kind_accuracy`.
- **Entrance** (`EntranceMetrics`): per aligned dungeon whose truth asserts `entrance` — reproduce geometry's selection from `SurveyIndex` (lowest-numbered non-empty level, first listed area), match when the truth key slug-matches the selected area's key, the truth level aligns to the selected extracted level, and the area is matched. Counts: `asserted`, `matched`, `accuracy`.
- **Encounter precision** (on `EncounterMetrics`): over matched areas whose truth asserts encounters — `precision_denominator` = distinct folded extracted names, `precision_matched` = those whose fold appears in the truth area's fold set, `precision`. Name-level, mirroring recall; the recall denominator's meaning is unchanged by construction. Expected landing (measured on the retained sweep-1 caches under full assertion): JN1 ≈ 0.93, JN2 ≈ 0.98 — a near-ceiling family; its band row will matter more than its headline.
- **`ModuleMetrics`** gains required `doors`, `transitions`, `entrances` fields and the extended `EncounterMetrics` — required, no defaults: the scoreboard is a run record and every entry regenerates in this phase (the phase 5 ruling; no CI test loads the committed board). `ByomEntry` takes the same model change with no data migration (the board file still doesn't exist). `corpus_means` and `run_eval.py`'s `_print_metrics` carry the new families.

### 3. Tests

- Truth-model tests: each new validator's accept/reject pairs, the asserted-empty flip (omitted ≠ `[]`), door-contradiction rejection, transition endpoint existence.
- Scorer tests per family over synthetic fixtures: the JN2 collapsed-level shape, the cross-level keyed shape, the level-shaped stub, door dedup and kind-conflict resolution, entrance mismatch, precision with hallucinated names.
- The pinned JN1 CI baseline (`test_jn1_pinned_baseline_over_the_committed_caches`) re-blessed with the new families' exact values over the committed test caches, movement explained in the comment as phase 7 did.
- The determinism test covers the new families; the full-assertion test extension from work item 1.

### 4. The truth passes — every scoreable member, in-phase

- **Committed members** (JN1, JN2, minimod): author `doors` over the ~92 asserted-connection areas, `transitions` and `entrance` per dungeon, and explicit `encounters: []` on every verified-empty area (~105 areas) — all from the printed pages under the `AUTHORING.md` independence line. Authoring agents receive the module PDF's pages (text and renders) and the existing truth file, never a `stages/` directory; the retained workdirs are off-limits to authors. Inline YAML comments on every judgment call, as the conventions require.
- **Adversarial verification**: a fresh-context second agent per member re-checks every new assertion against its cited page and hunts omissions; disagreements resolve against the printed page. `truth_provenance.verified` gains a phase 9 entry per member — appended, not rewritten (JN1/JN2's records honestly predate the adversarial pass; this phase gives the committed corpus its first real one).
- **BYOM members** (the four with retained workdirs): same passes at the BYOM partial-truth posture — new families asserted where the existing truth's page coverage reaches, honestly omitted elsewhere. PDFs and hashes per `~/Documents/osr-forge-byom/SOURCES.md`; truth files, sidecars, and packets stay in the private corpus directory, aggregate counts only in anything committed.
- **Owner sampling**: the phase 9 assertion delta (per member: the new keys, the judgment-call flags) files as a GitHub issue assigned to the owner, referencing the standing issue #24 — the phase does not wait on it.

### 5. The offline re-score, the band, the amendment

- Re-score all six committed-corpus retained workdirs (`sweep1-`/`sweep2-` × 3) against the extended truth. Hard check first: the four existing families reproduce the committed scoreboard and the phase 7 amendment pair exactly — any drift is a scorer bug. Then: scoreboard entries regenerate from the sweep-1 runs (run blocks unchanged — same runs, re-scored; `truth_sha256` re-pinned at score time), the new families' bands come from the observed pair spread floored at 0.02, and the amendment records the full pair table plus the flip-check dungeon counts, phase 6/7 style.
- Re-score the four BYOM workdirs; the private corpus scoreboard updates in place. The committed BYOM board still does not exist (blocked on owner sampling, unchanged by this phase).
- `tools/eval/README.md`: the band table gains rows (door recall, door precision, door kind accuracy, door locked accuracy, transition recall, transition precision, transition kind accuracy, entrance accuracy, encounter precision); the conventions section gains the four families' authoring rules; the "four pinned metric families" phrasing updates everywhere it appears.

### 6. The contingent member — search, verify, join or record

- Candidate criteria, pinned: non-BFRPG layout conventions, freely licensed with a verifiable grant (the two-leg phase 0 pattern: the PDF's own license statement, plus the grant's reliability — consent lists, sole-copyright, or equivalent — checked against citable, dated sources), B/X-compatible keyed dungeon content the truth conventions can express, and small enough that its double sweep stays in single-digit dollars (the JN1/JN2 precedent: ≈ $1.2–1.5 per run).
- On a pass: manifest (pinned `sha256`, `license.verified` write-up on the JN1 template, `truth_provenance`), full truth including the new families, authored + adversarially verified like every member, then its own live double sweep — the phase's only live spend — with its pair recorded in the amendment and its entry on the scoreboard.
- On failure: every evaluated candidate and its disqualifying fact recorded in the amendment and summarized in `tools/eval/README.md` beside the BF1 record; the spec's open question stays open. Either outcome closes the work item — the phase never blocks on it.

### 7. Docs and changelog

- `AUTHORING.md`: the new families' conventions (door assertion rules, the undirected transition convention, entrance, asserted-empty encounters); the encounters-asymmetry paragraph rewritten as resolved; the process list and committed-posture sections updated.
- `docs/evals.md` family list, glossary entries for the new terms of art (asserted-empty, edge-fact seam, dungeon-scoped matching), the spec sentence (spec impact above), and the `[Unreleased]` changelog bullet (the scorer is published package code; its metric surface is user-visible).

## Sequencing

1 (schema + validators) → 2 + 3 (scorer + tests, together — the synthetic fixtures drive the computations) → 4 (truth passes; authoring starts once 1 merges locally since the validators gate authoring output) → 5 (re-score + band + amendment; requires 2 and 4) → 7 (docs land with the implementation PR). 6 (the contingent member) starts any time after 1–3 and must resolve — either way — before the phase closes. The whole phase is one implementation PR after the plan PR, per the loop; the truth passes and re-score happen on the implementation branch so the scoreboard, truth files, and scorer land atomically.

## Definition of done

- The seven-family scorer merges with the pinned JN1 baseline green at zero network and the existing four families reproducing the standing pair exactly.
- Every scoreable corpus member — three committed, four BYOM — carries the new assertions, adversarially verified, provenance appended.
- The committed scoreboard and band table carry the new families from the re-scored standing pair; the amendment records the pair and the BYOM re-scores.
- The owner-sampling issue for the phase 9 delta exists and is assigned.
- The contingent-member work item is closed with a join (manifest, truth, double sweep, scoreboard entry) or a record (candidates and disqualifying facts, README note).
- Docs updated as in work item 7; changelog bullet present; `ruff format --check`, `ruff check`, `pyright`, `pytest` green.

## Amendments

(Recorded during implementation.)
