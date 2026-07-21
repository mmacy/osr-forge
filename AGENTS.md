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
- Live model runs (the extraction runner, the eval sweep) need the `OSRFORGE_FOUNDRY_*` environment variables (see the README's provider table). When they aren't set, derive them from the authenticated Azure CLI session instead of asking: discover the Azure OpenAI resource with `az cognitiveservices account list`, its deployments with `az cognitiveservices account deployment list`, and a key with `az cognitiveservices account keys list`; export the values into the process environment only. Never write credentials, keys, endpoints, or resource names into the repository — this file records the method, nothing more.

## Greenfield discipline

Refactor freely and update every call site — tests are the safety net. No re-exports or aliases kept to preserve an old import path, no deprecation scaffolding, no code kept "just in case" — git history is the archive. The exception is the artifact contracts (`adventure.json` stamped document, `report.json` flag and finding vocabularies, `overrides.yaml` schema): external consumers read these, and they are a public API, additive-only within a schema version. That fence is owner-suspended (declared 2026-07-16) while no external consumers exist — the only consumers today are the owner's own projects — and the owner's declaration governs until they deem external consumers to exist.

## Standing obligations

- **The eval regression rule.** Any PR that edits extraction prompts or schemas, `MONSTER_ALIASES`, resolution logic, or the model deployment re-runs the eval sweep (`tools/eval/README.md`) and commits the updated `tools/eval/corpus/scoreboard.json` in the same PR — the same edits that strand fixtures re-measure quality: one workflow, two obligations (the fixture re-record rule lives in `tools/extract/README.md` and `tests/assets/README.md`). A PR that changes the scorer's matching or metric semantics carries the offline counterpart: re-score the standing sweep pair, refresh the band, record the pair in the phase amendment. A metric dropping by more than the recorded noise band (the living table in `tools/eval/README.md`) requires an explicit justification in the PR description; silence is a blocked merge.
- **The BYOM scoreboard refresh.** Entries on `tools/eval/byom-scoreboard.json` refresh best-effort by whoever owns the module — contributors cannot re-run sweeps over modules they don't own, so BYOM entries never gate a merge. A stale entry is visible via its `osrforge_version` stamp, never blocking. The owner's BYOM sources live outside this repo: the private corpus at `~/Documents/osr-forge-byom/` holds each member's manifest plus `source.sha256` sidecar, and `SOURCES.md` there records the local retail-PDF paths and hashes — the licensing fence keeps those specifics out of this repository, so this line records only where to look.
- **Truth-semantics migrations complete in-phase.** A phase that changes what a truth file can assert — a new metric, a new assertion key, a matching-convention change — finishes the corresponding truth passes over every corpus member, committed and BYOM alike, before the phase closes. Those passes are agent work under `tools/eval/AUTHORING.md` (agents author, an adversarial pass verifies; the runbook's independence line, not a human's hands, is what makes truth trustworthy) — a phase must not end by relabeling them "owner work," which is how phase 7 left the HotO/B3/B4 custom passes dangling. The one leg that legitimately remains out-of-band is owner sampling, and it leaves the phase as a GitHub issue assigned to the module owner — never as a prose note in a handoff.
- **Changelog discipline.** A PR that changes user-visible behavior adds its bullet to the `[Unreleased]` section of `CHANGELOG.md` in the same PR. A release renames that section to the version and date.

## Releasing

- The version lives in `pyproject.toml` alone; `osrforge.versioning.osrforge_version()` reads installed metadata at runtime. The bump procedure: edit the version, run `uv lock`, and nothing else — the goldens re-bless deliberately on version bumps per `tests/assets/README.md`.
- A release is an annotated `vX.Y.Z` tag on the merge commit (`git tag -a vX.Y.Z -m "osr-forge X.Y.Z"`, then push the tag). `release.yml` does the rest: fails fast if the tag doesn't match the pyproject version, re-runs the full standing gate plus the strict docs build, builds once, audits the artifacts with `tools/release/check_dist.py` (the licensing fence, machine-checked: no `tests/` or `tools/` content, no PDFs, renders, or fixtures in the wheel or sdist), smoke-tests the wheel in a fresh venv on both OSes with `tools/release/install_smoke.py`, publishes to PyPI via trusted publishing (no tokens anywhere in the repository), and creates the GitHub Release from the tagged version's changelog section.
- The local dry run before tagging: `uv build`, then `python3 tools/release/check_dist.py dist X.Y.Z`, then install the wheel into a fresh venv and run `tools/release/install_smoke.py X.Y.Z` with that venv's interpreter.
- Recovery: any failure before the publish job leaves PyPI untouched — delete the tag, fix on a branch, re-tag. Once publish succeeds, that version's filenames are burned on PyPI and the next attempt is a new version.
- One-time setup, completed during the 0.1.0 release (2026-07-20): the PyPI pending publisher for project `osr-forge` (workflow `release.yml`, environment `pypi`) and the matching `pypi` environment in the GitHub repo. The Pages source ("GitHub Actions") was set earlier, when docs deploy first landed. Provenance correction: a prior version of this line recorded the pending publisher and the `pypi` environment as already-done state — they were not; both were created as part of shipping 0.1.0.
- Versioned documentation is not adopted; Pages-from-`main` is the whole deployment. The adoption trigger, carried from osrlib verbatim: the first post-1.0 release whose published docs must describe behavior different from `main` adopts mike or equivalent in that release's own plan. Patch and docs-only releases do not trigger it.

## Invariants the spec imposes

- **Assembly purity.** `adventure.json`, `report.json`, and previews are a deterministic function of cached stage outputs plus `overrides.yaml`. LLM calls happen only in the extraction stages; correcting a draft never re-rolls the model.
- **Provider isolation.** Pipeline code never imports a vendor SDK; all model access goes through the `ModelProvider` protocol. Vendor specifics (Azure AI Foundry, `gpt-5.4`) live in adapters only.
- **No network in tests.** Unit and pipeline tests run against `FixtureProvider` recordings; live-model calls happen only in the on-demand eval harness, never in CI.
- **osrlib stays outside.** osr-forge depends on osrlib; nothing here is a candidate for merging into it (osrlib is sans-I/O and frozen-API).

## Licensing

Package code is MIT. osr-forge ships no game content — osrlib's OGL data stays in osrlib, and the eval corpus references adventures by pointer + hash, never by bundled PDF.

The fences bind the repo and the wheel, not users: converting a privately owned, non-redistributable module locally is the primary use case and must never be constrained by them. The design corollary: no pipeline feature may persist module text outside the user's workdir — anything that does (fixture recording) stays opt-in, developer-facing, and out of the conversion path. The BYOM measurement fence is the same rule applied to evals (`tools/eval/AUTHORING.md`): truth files, sidecars, and workdirs for retail modules stay in the owner's private corpus directory, and the committed BYOM scoreboard carries only aggregate counts and ratios — no retail-derived text ever enters the repository, permanently.
