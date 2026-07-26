---
name: phase-loop
description: Run the osr-forge phase loop — plan a roadmap phase, implement one, and drive the rubber-duck review to SOLID. Use when asked to work up a plan for phase N, implement the plan for phase N, or rubber-duck an artifact.
---

# The phase loop

Each roadmap phase in `docs/spec.md` ships as two PRs — a plan, then an implementation — and both follow the same create → rubber-duck → revise-until-solid → PR loop. "Work up a plan for phase N" or "implement the plan for phase N" means run this loop end to end, unprompted. The workflow mirrors osrlib-python's `AGENTS.md`; keep parity with it unless that file says otherwise.

## Planning a phase

1. Research first: the phase's roadmap entry and every contract it touches in `docs/spec.md`, the prior phase plans in `docs/`, the existing code, and the osrlib surfaces the phase consumes. Hazards found during research (model quirks, PDF edge cases, osrlib validation behavior) belong in the plan so the implementer doesn't rediscover them.
2. Write `docs/phase-N-plan.md` following the structure of the prior plans: intro with the spec milestone, scope (in and out, naming the phase that picks up each deferral), work items, sequencing, definition of done. Plans are decision-complete: every choice an implementer would otherwise guess at is pinned with a rationale.
3. Branch `phase-N-plan`; commit the draft as `add phase N implementation plan (pre-review draft)`.
4. Rubber-duck it (below), revise until SOLID, open the PR.

## Implementing a phase

The same loop on branch `phase-N-impl`: implement to the plan with tests green, commit, rubber-duck the result, and address findings as `address rubber-duck review findings`. The plan is the contract — when implementation reveals the plan was wrong or silent, amend the plan document on the same branch (`amend phase N plan: ...`) so plan and code never diverge.

## The rubber-duck loop

- Spawn a fresh subagent as a skeptical senior reviewer. Give it an ordered reading list — the spec, prior plans, `AGENTS.md`, the artifact under review, the relevant code, and the osrlib models or docs the work touches — and require evidence: every finding must quote the spec, the code, or the artifact, be ranked blocking vs non-blocking, and the review must end in a verdict (SOLID or NEEDS REVISION) plus a verified-good list of claims it actively checked.
- The reviewer's mandate covers design hygiene, not just spec fidelity: it must hunt for the greenfield anti-patterns in `AGENTS.md` (back-compat shims, dual import paths, deprecation scaffolding, dead accommodation code) and flag any it finds.
- Judge findings on the merits. Verify disputed claims against the spec, osrlib, or the code yourself; push back on findings that are wrong instead of deferring to the duck. Address what survives and commit as `revise phase N plan per rubber-duck review` (or the address-findings message above).
- Send the revision back to the same reviewer, context intact, for re-verification of each fix. Loop until SOLID. Fold in any sign-off notes.
- Commits tell the honest story — draft, revision(s), sign-off tweaks — and the PR description summarizes the notable decisions plus the review provenance (what the duck found, what changed).
