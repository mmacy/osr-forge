# Extraction runner

Drives real `preprocess` → `survey` → `content` runs with `FoundryProvider`.
Manual, live-network, repo-only — never packaged, never in CI, exactly like
`tools/spike/`. Requires the `OSRFORGE_FOUNDRY_ENDPOINT` and
`OSRFORGE_FOUNDRY_DEPLOYMENT` environment variables (plus
`OSRFORGE_FOUNDRY_API_KEY` for key auth; without it, Entra ID via
`DefaultAzureCredential`).

Recording is opt-in via `--record-fixtures`: fixture request digests embed
module text verbatim, so verification runs over non-redistributable modules
must run without it (or point it outside the repo).

## Re-recording rule

Any prompt or schema edit changes request fingerprints and strands every
recorded fixture — re-record with the commands below, and never rebuild
fixture requests from fresh page renders: PNG bytes are stable only for a
locked pdfium+Pillow pair, so a dependency bump strands them too. Each fixture
set is committed together with the exact page renders it was recorded against.

## The minimod recording session

Records real `survey()` + `content()` runs over the CC0 minimod, then commits
the exact page renders, the fixtures, and the golden stage caches the
pipeline-replay test pins byte-for-byte:

```sh
uv run tools/extract/run_extraction.py full tests/assets/minimod/minimod.pdf \
    --workdir minimod.forge \
    --record-fixtures tests/assets/minimod/fixtures
mkdir -p tests/assets/minimod/pages tests/assets/minimod/expected
cp minimod.forge/pages/* tests/assets/minimod/pages/
cp minimod.forge/stages/*.json tests/assets/minimod/expected/
rm -rf minimod.forge
```

## The chaotic-caves excerpt session (replay-grade)

Records the real phase 1 survey prompt and the first content batch over the
already-committed 8-page subset. Page parts come exclusively from
`tests/assets/chaotic-caves/pages/`; the survey's page references are filtered
down to that subset before batch planning, so the recorded chain is closed
over committed pages and replays with zero network:

```sh
uv run tools/extract/run_extraction.py excerpt \
    --module-dir tests/assets/chaotic-caves \
    --page-count 48 \
    --record-fixtures tests/assets/chaotic-caves/fixtures-extract/replay
```

## The JN1 milestone session (evidence-grade)

The full 48-page run over the local, gitignored, sha256-verified PDF. Its
fixtures and the produced stage caches are committed as milestone evidence —
no replay promise:

```sh
uv run tools/extract/run_extraction.py full \
    tests/assets/chaotic-caves/JN1-Chaotic-Caves-r28.pdf \
    --workdir jn1.forge \
    --record-fixtures tests/assets/chaotic-caves/fixtures-extract/evidence
mkdir -p tests/assets/chaotic-caves/stages
cp jn1.forge/stages/*.json tests/assets/chaotic-caves/stages/
rm -rf jn1.forge
```

## Verification runs (nothing committed)

Licensed, non-redistributable modules run live with recording off; evidence
lands in the phase 1 plan's amendment as pointer + hash + metrics only:

```sh
uv run tools/extract/run_extraction.py full ~/Documents/The_Hole_in_the_Oak.pdf --workdir hole.forge
```
