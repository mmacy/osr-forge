# Testing

The whole test suite runs at zero network and zero model spend, in seconds.
That is a hard invariant, not an aspiration: unit and pipeline tests run
against recorded fixtures, live model calls happen only in the on-demand
tooling, and CI never spends a token.

## Fixtures: recorded model exchanges

A [fixture](../reference/glossary.md#fixture) is one recorded
request/response exchange, written by
[`RecordingProvider`][osrforge.providers.fixtures.RecordingProvider] during a
real run and replayed by
[`FixtureProvider`][osrforge.providers.fixtures.FixtureProvider] in tests.
Lookup is by
[request fingerprint](../reference/glossary.md#request-fingerprint) — the
sha256 identity of the request's tag, system text, content parts, and JSON
Schema — so a replayed test exercises the *exact* request the pipeline builds
today. If the pipeline's request drifts from the recording, the test fails
loudly with a fixture miss instead of silently replaying a stale answer, and
replayed data is re-validated against the incoming request's schema.

The request builders (for example
[`build_survey_request`][osrforge.survey.build_survey_request] and
[`build_monsters_request`][osrforge.monsters.build_monsters_request]) are
public and pure for exactly this reason: the pipeline and the fixture tests
must build fingerprint-identical requests without duplicating prompt code.

## The re-record rule

Editing an extraction prompt, a request schema, or the
[`MONSTER_ALIASES`][osrforge.monsters.MONSTER_ALIASES] table changes request
fingerprints and strands every recorded fixture that request participates
in. So does regenerating the test PDFs or bumping the PDF-render
dependencies, because fingerprints hash the page bytes. The remedy is always
to re-record the whole coupled set with the documented session commands —
`tools/extract/README.md` in the repository is the authority — never to
rebuild fixture requests from fresh renders or hand-edit a fixture file.

The same edits carry a second obligation: re-run the eval sweep and commit
the updated scoreboard in the same PR (`tools/eval/README.md`). One
workflow, two obligations — the change that strands fixtures is the change
that moves quality, so it re-measures quality.

## Goldens and byte-stability

Pipeline tests pin full-chain output byte-for-byte:
[goldens](../reference/glossary.md#goldens) committed under `tests/assets/`
cover the stage caches, `adventure.json`, `report.json`, and the previews for
each test module. Two asset sets carry the suite:

- **minimod** — *The Root Cellar of Old Wenna*, a CC0 mini-module authored
  for this repository, whose committed PDF, page assets, fixtures, and
  goldens make the full conversion replayable end to end.
- **chaotic-caves** — *JN1 The Chaotic Caves*, the real-module evidence set,
  including the correction-loop session: committed stage caches, a real
  `overrides.yaml`, and corrected goldens the milestone test re-assembles
  byte-for-byte.

Fixture sets are either
[replay-grade or evidence-grade](../reference/glossary.md#replay-grade):
replay-grade sets are closed over committed assets and back tests forever;
evidence-grade sets document a live run over content the repository cannot
commit. Goldens are regenerated deliberately via the fabrication commands in
`tests/assets/README.md` — a version bump re-blesses the stamped envelopes on
purpose — and never hand-edited.

## Evals are not tests

The eval harness (`tools/eval/` in the repository, explained in
[evals](../evals.md)) measures extraction quality with live model runs over a
corpus with verified ground truth. It is on-demand, costs real money, and
never runs in CI. The *scorer* itself is the opposite:
[`osrforge.evals`][] is deterministic, fully unit-tested package code, and
the pinned JN1 baseline scores in CI at zero network. Ground truth is
authored under
[truth independence](../reference/glossary.md#truth-independence) — from the
printed module, never from pipeline output.

## Running the suite

```sh
uv run pytest            # the whole suite: zero network, seconds
uv run pytest tests/test_monsters.py -k fuzzy
```

If a test asks for the network, that is a bug — either yours (a code path
reached a live provider) or a stranded fixture (see the re-record rule
above). A `FixtureMissError` names the request tag and fingerprint it
wanted; compare against the recorded tags it lists.
