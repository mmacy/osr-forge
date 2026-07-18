# Contributing

Everything a new contributor needs to go from a fresh clone to a passing
development loop. The [architecture](architecture.md) page explains how the
code is organized; [testing](testing.md) explains the fixture-and-goldens
model; the [glossary](../reference/glossary.md) defines the project's terms
of art.

## Setup

osr-forge requires Python ≥ 3.14 and uses [uv](https://docs.astral.sh/uv/)
exclusively — never `pip` directly:

```sh
git clone https://github.com/mmacy/osr-forge
cd osr-forge
uv sync
uv run pytest
```

The full suite runs at zero network in seconds — if it doesn't, see
[testing](testing.md).

## The gates

Every change passes all five before it merges:

```sh
uv run ruff format          # formatting
uv run ruff check           # linting
uv run pyright              # type checking
uv run pytest               # tests
uv run mkdocs build --strict  # docs: broken links and cross-references fail
```

The strict docs build renders the API reference from the source docstrings,
so a docstring cross-reference that doesn't resolve fails the build — the
docs are part of the gate, not an afterthought.

## Style

- Google-style docstrings written in Markdown, rendered by mkdocstrings.
  Cross-reference other code with the `` [`name`][package.module.name] ``
  pattern — it resolves across pages, into osrlib's published reference, and
  into the Python and pydantic inventories.
- Built-in generics (`list[str]`, `dict[str, int]`); no
  `from __future__ import annotations`; maximum line length 120.
- User-facing docs pages use sentence-case headings and link terms of art to
  the [glossary](../reference/glossary.md) on first use.

## The invariants

Four rules shape every change; the [architecture](architecture.md) page
shows where each one lives in the code:

- **Assembly is pure.** `adventure.json`, `report.json`, and the previews
  are a deterministic function of the cached stage outputs plus
  `overrides.yaml`. Nothing downstream of the
  [stage caches](../reference/glossary.md#stage-cache) may call a model.
- **Provider isolation.** Pipeline code never imports a vendor SDK; all
  model access goes through the
  [`ModelProvider`][osrforge.providers.base.ModelProvider] protocol.
- **No network in tests.** Tests run on recorded fixtures; live runs happen
  only in the on-demand tooling. See [testing](testing.md).
- **osrlib stays outside.** osr-forge depends on
  [osrlib](https://mmacy.github.io/osrlib-python/) and validates through it;
  nothing here re-implements or forks its validation.

## Working discipline

- **Refactor freely; update every call site.** No back-compat shims, no
  re-exports kept for old import paths, no code kept "just in case" — git
  history is the archive. The exception is the artifact contracts
  (`adventure.json`, `report.json` vocabularies, `overrides.yaml` schema):
  external consumers read those, and they grow additively within a schema
  version.
- **Prompt and schema edits carry obligations.** Editing an extraction
  prompt, request schema, or the alias table strands recorded fixtures and
  requires an eval re-run — [testing](testing.md#the-re-record-rule) has the
  full rule.
- **User-visible changes add a changelog bullet** to the `[Unreleased]`
  section of `CHANGELOG.md` in the same PR.

## The repository beyond `src/`

- `tests/` — the suite and its committed assets (fixtures, goldens, the CC0
  minimod). `tests/assets/README.md` documents every asset's provenance.
- `tools/` — on-demand tooling, never packaged: the extraction runner
  (`extract/`, records fixtures from live runs), the eval harness (`eval/`),
  the minimod generator (`minimod/`), release checks (`release/`), and the
  docs generators (`docs/`).
- `docs/` — this site, plus the project's internal design history
  (`spec.md` and the per-phase plan documents). The history is deliberately
  unpublished — it records how decisions were reached, in development order;
  the published pages carry everything durable. When you need a decision's
  full original rationale, read it in the repository.

## Licensing fences

Package code is MIT, and osr-forge ships no game content: the wheel contains
no PDFs, fixtures, or module text, and the eval corpus references adventures
by pointer and hash. The fences bind the repository and the wheel, not users
— converting a privately owned module locally is the primary use case. The
practical consequence for contributors: no pipeline feature may persist
module text outside the user's workdir, and anything that does (fixture
recording) stays opt-in and out of the conversion path. See
[licensing](../licensing.md).
