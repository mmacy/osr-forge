# Agent guide for osr-forge

osr-forge is a standalone Python package and CLI that converts tabletop adventure module PDFs into playable [osrlib](https://github.com/mmacy/osrlib-python) `Adventure` documents: an LLM-assisted extraction pipeline, deterministic geometry synthesis, a human correction loop built on an overrides file, and validation against the real osrlib models.

## Start here

- `docs/spec.md` is the single source of truth. Read it before any implementation work. It is decision-complete: contracts, pipeline architecture, and a phased roadmap. Implement in phase order.
- osrlib (checked out at `~/repos/osrlib-python`, published as `osrlib`) is the downstream authority. Converted output must load through the pinned osrlib (`check_document`, `Adventure.model_validate`, `validate_adventure`) — never re-implement or fork its validation. When an output-format question comes up, osrlib's models and documentation are the authority; verify against them rather than working from memory.
- Never hand-edit generated artifacts (`adventure.json`, `report.json`, previews). Corrections belong in `overrides.yaml` — assembly purity is the core invariant.

## The phase loop

Each roadmap phase in `docs/spec.md` ships as two PRs — a plan, then an implementation — and both follow the same create → rubber-duck → revise-until-solid → PR loop. "Work up a plan for phase N" or "implement the plan for phase N" means run this loop end to end, unprompted. The workflow mirrors osrlib-python's `AGENTS.md`; keep parity with it unless this file says otherwise.

### Planning a phase

1. Research first: the phase's roadmap entry and every contract it touches in `docs/spec.md`, the prior phase plans in `docs/`, the existing code, and the osrlib surfaces the phase consumes. Hazards found during research (model quirks, PDF edge cases, osrlib validation behavior) belong in the plan so the implementer doesn't rediscover them.
2. Write `docs/phase-N-plan.md` following the structure of the prior plans: intro with the spec milestone, scope (in and out, naming the phase that picks up each deferral), work items, sequencing, definition of done. Plans are decision-complete: every choice an implementer would otherwise guess at is pinned with a rationale.
3. Branch `phase-N-plan`; commit the draft as `add phase N implementation plan (pre-review draft)`.
4. Rubber-duck it (below), revise until SOLID, open the PR.

### Implementing a phase

The same loop on branch `phase-N-impl`: implement to the plan with tests green, commit, rubber-duck the result, and address findings as `address rubber-duck review findings`. The plan is the contract — when implementation reveals the plan was wrong or silent, amend the plan document on the same branch (`amend phase N plan: ...`) so plan and code never diverge.

### The rubber-duck loop

- Spawn a fresh subagent as a skeptical senior reviewer. Give it an ordered reading list — the spec, prior plans, this file, the artifact under review, the relevant code, and the osrlib models or docs the work touches — and require evidence: every finding must quote the spec, the code, or the artifact, be ranked blocking vs non-blocking, and the review must end in a verdict (SOLID or NEEDS REVISION) plus a verified-good list of claims it actively checked.
- The reviewer's mandate covers design hygiene, not just spec fidelity: it must hunt for the greenfield anti-patterns below (back-compat shims, dual import paths, deprecation scaffolding, dead accommodation code) and flag any it finds.
- Judge findings on the merits. Verify disputed claims against the spec, osrlib, or the code yourself; push back on findings that are wrong instead of deferring to the duck. Address what survives and commit as `revise phase N plan per rubber-duck review` (or the address-findings message above).
- Send the revision back to the same reviewer, context intact, for re-verification of each fix. Loop until SOLID. Fold in any sign-off notes.
- Commits tell the honest story — draft, revision(s), sign-off tweaks — and the PR description summarizes the notable decisions plus the review provenance (what the duck found, what changed).

## Toolchain

- Python ≥ 3.14. Package management with `uv` exclusively (`uv add`, `uv sync`, `uv run`) — never `pip`.
- Format with `ruff format`, lint with `ruff check`, type-check with `pyright`, test with `pytest` (not unittest).
- Type hints use built-in generics (`list[str]`, `dict[str, int]`). Do not import `List`/`Dict`/`Tuple` from `typing` and do not use `from __future__ import annotations`.
- Docstrings are Google style, written in Markdown. Maximum line length 120.

## Greenfield discipline

osr-forge is pre-release: there is no frozen public API yet, so refactor freely and update every call site — tests are the safety net. No re-exports or aliases kept to preserve an old import path, no deprecation scaffolding, no code kept "just in case" — git history is the archive. The exception is the artifact contracts (`adventure.json` stamped document, `report.json` flag vocabulary, `overrides.yaml` schema): external consumers read these, so once osr-web integrates, treat them like a public API — additive-only within a version.

## Invariants the spec imposes

- **Assembly purity.** `adventure.json`, `report.json`, and previews are a deterministic function of cached stage outputs plus `overrides.yaml`. LLM calls happen only in the extraction stages; correcting a draft never re-rolls the model.
- **Provider isolation.** Pipeline code never imports a vendor SDK; all model access goes through the `ModelProvider` protocol. Vendor specifics (Azure AI Foundry, `gpt-5.4`) live in adapters only.
- **No network in tests.** Unit and pipeline tests run against `FixtureProvider` recordings; live-model calls happen only in the on-demand eval harness, never in CI.
- **osrlib stays outside.** osr-forge depends on osrlib; nothing here is a candidate for merging into it (osrlib is sans-I/O and frozen-API).

## Licensing

Package code is MIT. osr-forge ships no game content — osrlib's OGL data stays in osrlib, and the eval corpus references adventures by pointer + hash, never by bundled PDF.

The fences bind the repo and the wheel, not users: converting a privately owned, non-redistributable module locally is the primary use case and must never be constrained by them. The design corollary: no pipeline feature may persist module text outside the user's workdir — anything that does (fixture recording) stays opt-in, developer-facing, and out of the conversion path.
