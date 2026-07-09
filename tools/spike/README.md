# The Foundry capability spike

Manual, live-network probes against the real Azure AI Foundry deployment.
Repo-only: never packaged, never run in CI. The probes drive the real package —
`preprocess()` on the spike module, then requests through
`RecordingProvider(FoundryProvider(...))` — and every claim in
`docs/foundry-capabilities.md` must trace to a fixture these probes recorded.

## Prerequisites

1. **Module selection and licensing gate** (phase 0 plan, work item 2). Before
   recording anything: pick a short (≤ 32 pages, single dungeon) Basic Fantasy
   RPG adventure — candidate: JN1 The Chaotic Caves — and confirm the PDF's own
   license page states CC BY-SA 4.0 and its contributors appear on the BFRPG
   project's consent list. Commit the PDF as
   `tests/assets/<module>/<module>.pdf` with a README carrying the CC BY-SA 4.0
   notice, attribution, source URL, and the PDF's sha256.
2. **Credentials.** Export:

   ```sh
   export OSRFORGE_FOUNDRY_ENDPOINT="https://<resource>.openai.azure.com"
   export OSRFORGE_FOUNDRY_DEPLOYMENT="<gpt-5.4 deployment name>"
   export OSRFORGE_FOUNDRY_API_KEY="<key>"   # omit to exercise Entra ID auth
   ```

## Running

```sh
uv run tools/spike/probes.py prepare  --module-dir tests/assets/<module>   # preprocess + commit page subset
uv run tools/spike/probes.py structured --module-dir tests/assets/<module> # probe 1: schemas
uv run tools/spike/probes.py images   --module-dir tests/assets/<module>   # probe 2: image ceilings + DPI cost
uv run tools/spike/probes.py context  --module-dir tests/assets/<module>   # probe 3: whole-module context
uv run tools/spike/probes.py extract  --module-dir tests/assets/<module>   # probe 4: extraction smokes
uv run tools/spike/probes.py auth     --module-dir tests/assets/<module>   # probe 6: key + Entra, one each
```

Probe 5 (usage and cost) has no subcommand: every probe prints its token
usage, and the findings document records the deployment's published pricing.

## Fixture grades

- **Replay-grade** (`structured`, `extract`): built only from the committed
  page subset in `tests/assets/<module>/pages/` (at most 8 pages, copied there
  by `prepare`), so tests can replay them through `FixtureProvider` forever.
- **Evidence-grade** (`images`, `context`): boundary probes over workdir
  renders; their fixtures back claims in the findings doc but replay is not
  promised, and their page renders are not committed.

All fixtures land in `tests/assets/<module>/fixtures/`. Whole-directory budget
(PDF + pages + fixtures): 10 MiB.

## Deliverable

`docs/foundry-capabilities.md`: the model id string the service returns, the
API surface used, every limit found with the probe that found it, observed
costs, quirks, and a closing "phase 1 impacts" section stating the
survey/content chunk sizes and schema budget the findings support.
