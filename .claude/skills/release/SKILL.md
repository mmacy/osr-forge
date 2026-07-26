---
name: release
description: Cut an osr-forge release — version bump, the annotated tag that drives release.yml, the licensing audit in check_dist.py, the local dry run before tagging, and recovery when a release fails. Use when cutting a release, bumping the version, or working on release.yml.
---

# Releasing osr-forge

- The version lives in `pyproject.toml` alone; `osrforge.versioning.osrforge_version()` reads installed metadata at runtime. The bump procedure: edit the version, run `uv lock`, and nothing else — the goldens re-bless deliberately on version bumps per `tests/assets/README.md`.
- A release is an annotated `vX.Y.Z` tag on the merge commit (`git tag -a vX.Y.Z -m "osr-forge X.Y.Z"`, then push the tag). `release.yml` does the rest: fails fast if the tag doesn't match the pyproject version, re-runs the full standing gate plus the strict docs build, builds once, audits the artifacts with `tools/release/check_dist.py` (the licensing fence, machine-checked: no `tests/` or `tools/` content, no PDFs, renders, or fixtures in the wheel or sdist), smoke-tests the wheel in a fresh venv on both OSes with `tools/release/install_smoke.py`, publishes to PyPI via trusted publishing (no tokens anywhere in the repository), and creates the GitHub Release from the tagged version's changelog section.
- The local dry run before tagging: `uv build`, then `python3 tools/release/check_dist.py dist X.Y.Z`, then install the wheel into a fresh venv and run `tools/release/install_smoke.py X.Y.Z` with that venv's interpreter.
- Recovery: any failure before the publish job leaves PyPI untouched — delete the tag, fix on a branch, re-tag. Once publish succeeds, that version's filenames are burned on PyPI and the next attempt is a new version.
- One-time setup, completed during the 0.1.0 release (2026-07-20): the PyPI pending publisher for project `osr-forge` (workflow `release.yml`, environment `pypi`) and the matching `pypi` environment in the GitHub repo. The Pages source ("GitHub Actions") was set earlier, when docs deploy first landed. Provenance correction: a prior version of this line recorded the pending publisher and the `pypi` environment as already-done state — they were not; both were created as part of shipping 0.1.0.
- Versioned documentation is not adopted; Pages-from-`main` is the whole deployment. The adoption trigger, carried from osrlib verbatim: the first post-1.0 release whose published docs must describe behavior different from `main` adopts mike or equivalent in that release's own plan. Patch and docs-only releases do not trigger it.
