# Extraction runner

Drives real `preprocess` → `survey` → `content` → `monsters` → `assemble`
runs with `FoundryProvider`. Manual, live-network, repo-only — never
packaged, never in CI, exactly like `tools/spike/`. Requires the
`OSRFORGE_FOUNDRY_ENDPOINT` and `OSRFORGE_FOUNDRY_DEPLOYMENT` environment
variables (plus `OSRFORGE_FOUNDRY_API_KEY` for key auth; without it, Entra ID
via `DefaultAzureCredential`).

Recording is opt-in via `--record-fixtures`: fixture request digests embed
module text verbatim, so verification runs over non-redistributable modules
must run without it (or point it outside the repo).

## Re-recording rule

Any prompt or schema edit changes request fingerprints and strands every
recorded fixture — re-record with the commands below, and never rebuild
fixture requests from fresh page renders: PNG bytes are stable only for a
locked pdfium+Pillow pair, so a dependency bump strands them too. Each fixture
set is committed together with the exact page renders it was recorded against.
The same prompt, schema, or `MONSTER_ALIASES` edits that strand fixtures also
re-run the eval sweep and commit the updated scoreboard in the same PR — one
workflow, two obligations; see `tools/eval/README.md`.

## The minimod recording session

Records real `survey()` + `content()` runs over the CC0 minimod, then commits
the exact page renders, the fixtures, and the full-chain goldens the
pipeline-replay test pins byte-for-byte. The monsters stage records nothing —
minimod's whole name population resolves in the exact tier, so it makes no
model call:

```sh
uv run tools/extract/run_extraction.py full tests/assets/minimod/minimod.pdf \
    --workdir minimod.forge \
    --record-fixtures tests/assets/minimod/fixtures
mkdir -p tests/assets/minimod/pages tests/assets/minimod/expected/previews
cp minimod.forge/pages/* tests/assets/minimod/pages/
cp minimod.forge/stages/*.json tests/assets/minimod/expected/
cp minimod.forge/adventure.json minimod.forge/report.json tests/assets/minimod/expected/
cp minimod.forge/previews/* tests/assets/minimod/expected/previews/
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

## The JN1 monsters session (replay-grade)

Resolves the committed JN1 stage caches' encounter names, recording the one
LLM request (text-only — unresolved names plus catalog candidates, so it
replays with zero network from committed assets alone) and writing the
produced `monsters.json` beside the other caches. **Sequencing rule:** the
`MONSTER_ALIASES` table must be final before this session — a later alias
edit covering a JN1 name changes the request fingerprint and strands the
fixture (see the asset README's couplings section):

```sh
uv run tools/extract/run_extraction.py monsters \
    --stages-dir tests/assets/chaotic-caves/stages \
    --record-fixtures tests/assets/chaotic-caves/fixtures-extract/replay
```

Then produce the JN1 goldens by assembling over the committed caches
(`tests/test_jn1_chain.py` byte-compares against exactly this fabrication —
any drift fails there loudly) and commit them inside the fenced directory's
10 MiB budget:

```sh
uv run tools/extract/run_extraction.py goldens \
    --stages-dir tests/assets/chaotic-caves/stages \
    --out tests/assets/chaotic-caves/expected \
    --page-count 48
```

## The JN1 correction session (phase 3)

The corrected-goldens flow is the goldens flow plus the committed correction
file: `--overrides` copies it into the fabricated workdir before assembly, and
the command runs `check` after assembly, printing findings. With
`--overrides`, the written `report.json` is the post-`check` report (findings
merged — the corrected goldens' gate); without it, the post-assemble report
(the uncorrected gate). Re-bless `expected-corrected/` after any deliberate
change to assembly, overrides semantics, the lint, osrlib, or the correction
file itself — never hand-edit:

```sh
uv run tools/extract/run_extraction.py goldens \
    --stages-dir tests/assets/chaotic-caves/stages \
    --out tests/assets/chaotic-caves/expected-corrected \
    --page-count 48 \
    --overrides tests/assets/chaotic-caves/overrides.yaml
```

## The milestone TUI session

`run_converted_tui.py` runs osrlib's example TUI crawler (from the checkout at
`~/repos/osrlib-python`) with its `build_adventure()` swapped for a converted
`adventure.json` — the phase 2 milestone's literal substitution. Interactive,
or reproducible with the TUI's own `--script`:

```sh
uv run tools/extract/run_converted_tui.py jn1.forge/adventure.json --seed 11
```

The procedure is verified against converted minimod: enter, fight the keyed
entrance encounter, walk to a parsed treasure cache, take it, return to town
for the XP award, and sell the loot.

## Verification runs (nothing committed)

Licensed, non-redistributable modules run live with recording off; evidence
lands in the phase 1 plan's amendment as pointer + hash + metrics only:

```sh
uv run tools/extract/run_extraction.py full ~/Documents/The_Hole_in_the_Oak.pdf --workdir hole.forge
```
