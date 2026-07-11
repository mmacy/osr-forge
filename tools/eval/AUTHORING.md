# The truth-authoring runbook

How a trustworthy `truth.yaml` gets made: point an agent at this document and
a module PDF, and follow the discipline below. Nobody hand-authors complete
truth for a shelf of retail modules — the phase 4 sessions proved an agent can
(223 areas across JN1 and JN2, maps included, in one session), and this
runbook pins what made those sessions trustworthy so the result can be
repeated on any module. The trust basis is not "a human checked it"; it is
**an independent instrument produced it, an adversarial pass verified it, and
the owner sampled it.**

The structural conventions (what counts as a dungeon, how encounter names and
counts are recorded, the treasure `present` rules) live in
[`tools/eval/README.md`](README.md) and apply verbatim; this document is about
*process* — how to author so the measurement can be trusted.

## The independence line

The authoring context may contain the module PDF — its text layer, its
rendered pages, and its map crops — and **nothing the pipeline produced for
that module**: no `survey.json`, no stage caches, no `adventure.json`, no
`report.json`. Reading pipeline output before writing truth contaminates the
measurement with the thing being measured.

Truth is authored *before* any conversion output for the module is read, or
in a context that has never loaded one. The discipline was never "a human
must do it"; it is "the measuring instrument must be independent of the
system under test."

One admissible-reference clause, carried from phase 4's explicit sanction:
human-authored correction notes whose every claim was verified against the
printed pages (JN1's phase 3 overrides file) may inform judgment calls,
because their epistemic source is the printed page — pipeline artifacts
themselves never qualify.

## The cross-instrument rule — advisory by design

Prefer an authoring model from a **different family** than the extraction
deployment, and record what was actually used in
`truth_provenance.instrument`. An instrument correlated with the system under
test can share its blind spots; family diversity is the cheap mitigation.

This is a stated preference, not a gate: `publish` checks provenance
*presence*, and `instrument` is free text, because enforcement is impossible
and false assurance is worse than none. (The phase 4 truths happen to satisfy
it — authored by a different vendor's model than the `gpt-5.4` extraction
deployment — which is the posture to keep, recorded rather than assumed.)

## The process

Distilled from the phase 4 authoring sessions:

1. **Extract the text layer page by page.** Work from the printed page order;
   note the page each fact came from as you go.
2. **Render the printed maps and crop per keyed site.** The maps are part of
   the module's truth — connections and site boundaries come from text plus
   map together.
3. **Walk the key section by section, reconciling stat blocks.** The stat
   block is authoritative for creature names and counts; the prose is
   authoritative for what's actually *in* the area.
4. **Apply the conventions in `tools/eval/README.md` verbatim** — singular
   stat-block creature names, fixed counts only, the treasure `present`
   rules, dungeons per keyed adventuring site.
5. **Assert `connections` and `treasure` only where the complete fact set is
   pinned.** Partial truth is the designed norm, not a compromise (see
   below).
6. **Run the validators** — load the truth through `osrforge.evals.load_truth`
   (unknown keys, duplicate slugs, and malformed codes fail loudly), and
   check template ids against the osrlib catalog.
7. **Flag every judgment call inline** — a YAML comment on the line — for the
   verification pass and the owner sample.

## Partial truth

A truth file must cover **every keyed area** of every in-scope dungeon (area
keys are cheap and the recall metric needs the complete universe), but
`connections` and `treasure` are assertion-aware: omitting them means "not
asserted," and the scorer keeps those areas out of the respective
denominators. A time-boxed truth file covering all area keys plus a verified
sample of areas still yields exact area recall and honestly-denominated
treasure agreement.

Assert only what you verified completely: a half-checked `connections` list
or a skimmed treasure call is worse than an omission, because it puts a wrong
fact in a denominator. One known asymmetry, pinned so nobody wonders:
`encounters: []` and an omitted `encounters` are indistinguishable today
(both mean "none listed") — that only matters to a hallucination-guard metric
no phase has built, and the phase that builds one picks it up.

The committed corpus stays *fully* asserted — a repo test enforces it — so
the gating scoreboard's meaning cannot silently thin out. Partial truth is
for private (BYOM) corpora.

## The adversarial verification pass

Required before `publish` — it is the truth file's rubber-duck:

- A **second agent, fresh context**, bound by the same independence line
  (module PDF in, pipeline artifacts out).
- Re-checks **every recorded fact against its cited page** and hunts for
  omissions — areas the key lists that the truth lacks, encounters the stat
  blocks print that the truth skips.
- Disagreements resolve **against the printed page**, never by negotiation
  between agents.
- The pass and its outcome are recorded in `truth_provenance.verified`.

## The owner-sampling bar

The module's owner spot-checks **at least 10 areas or 10% (whichever is
larger) plus every flagged judgment call**, and the result lands in
`truth_provenance.verified`. Humans audit; agents author — the sample is the
one leg of the trust chain outside the authoring agents' correlation class,
so it cannot be delegated back to an agent. The step-by-step, with the
rationale spelled out, is the docs site's
[owner sampling guide](../../docs/guides/owner-sampling.md).

## Provenance, recorded

The module's `manifest.yaml` carries the record in its `truth_provenance`
block — `authored` (date), `instrument` (the authoring model/agent), and
`verified` (which legs actually ran: the adversarial pass, the owner sample,
any CI baselines). `publish` refuses a module whose manifest lacks the block:
unverified truth can be scored locally all day, but it cannot put numbers on
the committed board.

## The licensing fence

Nothing derived from a retail module's text ever enters the repository — the
truth file, the sidecar, and the workdir all stay in the owner's private
corpus directory, and the committed BYOM scoreboard carries only aggregate
counts and ratios (see `AGENTS.md` → Licensing). This is a permanent fence,
not a deferral.
