# JN1 The Chaotic Caves (spike module)

The phase 0 Foundry capability spike's real module: *JN1 The Chaotic Caves*,
Release 28, copyright © 2009–2015, 2018–2024 J.D. Neal, a
[Basic Fantasy Role-Playing Game](https://www.basicfantasy.org) adventure.

## License and what is committed

The module's own license page (page 2 of the PDF) states:

> All textual materials in this document, as well as all maps, floorplans,
> diagrams, charts, and forms included herein, are distributed under the terms
> of the Creative Commons Attribution-ShareAlike 4.0 International License.
> Most other artwork presented is property of the original artist and is used
> with permission. Note that you may not publish or otherwise distribute this
> work as is without permission of the original artists; you must remove all
> non-licensed artwork before doing so.

Because the as-published PDF contains that non-licensed artwork, **the PDF is
not committed**. This directory carries only content the license page places
under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) — page
text and pages containing only text and maps — plus recorded model fixtures
whose request digests embed module text (also licensed):

- `pages/` — the replay-grade page subset (renders + text layers) backing the
  replay-grade fixtures: page 8 (town key), pages 22–27 (the caves key), and
  page 38 (the keyed caves map). Each was visually verified to contain no
  non-licensed illustration before committing.
- `fixtures/` — recorded request/response fixtures from the phase 0 capability
  spike (see `docs/foundry-capabilities.md`). Replay-grade fixtures pair with
  `pages/`; evidence-grade fixtures (image-count, DPI, and context boundary
  probes) are committed without their page assets and replay is not promised.
- `fixtures-extract/` — extraction recordings, classified by directory:
  `replay/` holds the excerpt survey, first-content-batch, and monsters
  resolution fixtures recorded over committed assets (replayed in tests with
  zero network); `evidence/` holds the full 48-page milestone run's fixtures
  and the phase 7 stat-block transcription fixtures (no replay promise —
  their requests reference uncommitted workdir renders).
- `stages/` — the stage caches (`survey.json`, `areas.<dungeon>.<level>.json`,
  `monsters.json`, and phase 7's `statblocks.json`) the recording sessions
  produced; the credibility-floor test gates them. Their text derives from
  the module's licensed text. `statblocks.json` is evidence-grade — its
  producing requests embed uncommitted page renders — and is consumed
  deterministically by the goldens and the JN1 eval baseline.
- `overrides.yaml` — the phase 3 correction session's file: every entry a
  genuine correction with its reason, checked against the module's printed
  stat blocks and maps. It is itself a test asset — the milestone gate
  byte-compares `assemble` over caches + this file against
  `expected-corrected/`, so editing it re-blesses those goldens (see
  `tests/assets/README.md` for the command).
- `expected-corrected/` — the corrected goldens: the post-overrides
  `adventure.json` and `previews/`, plus the post-`check` `report.json`
  (findings merged; the session's accepted warnings byte-pinned).

## The JN1 monsters fixture's couplings

The phase 2 monsters recording (`fixtures-extract/replay/monsters.*.json`) is
replay-grade: the request is text-only — unresolved names plus candidate
lists, no page images — so tests reconstruct it from the committed stage
caches, the installed osrlib catalog, and the prompt code. Two couplings
follow:

- The candidate lists derive from the osrlib catalog, so an osrlib upgrade
  that changes the catalog strands the fixture. Acceptable: CI installs from
  the committed lockfile, and the golden compatibility gate fails first on any
  osrlib bump.
- The request's name population is exactly what resolution tiers 1-3 left
  unresolved, so a `MONSTER_ALIASES` edit covering a JN1 name changes the
  request fingerprint and strands the fixture too. The alias table is seeded
  before the recording session; later growth re-records.

Everything in this directory derived from the module is distributed under
CC BY-SA 4.0 with attribution to J.D. Neal and the Basic Fantasy Project.

## The source PDF

To re-record fixtures, place the exact source PDF in this directory (it is
gitignored):

- File: `JN1-Chaotic-Caves-r28.pdf` (48 pages, 15,769,968 bytes)
- sha256: `37e6325ad0ebd52077aedb9f7f247511709d80bb8a25e4dd8c95da83d2730240`
- Source: https://www.basicfantasy.org (Adventure Modules → JN1 The Chaotic
  Caves, Release 28)

## License verification record (2026-07-09)

- The PDF's license page states CC BY-SA 4.0 for all textual materials, maps,
  floorplans, diagrams, charts, and forms (quoted above), with the artwork
  carve-out that keeps the as-published PDF out of this repo.
- The sole copyright holder, J.D. Neal, appears on the Basic Fantasy Project's
  [contributor consent list](https://www.basicfantasy.org/consent-list.html)
  (verified via the Wayback Machine snapshot of 2025-04-18; basicfantasy.org
  itself sits behind a bot challenge). Credited text editors Tom Hoyt, James
  Lemon, and Alan Vetter also appear. Credited artists Alexander Cook, Tomas
  Arfert, Erik Wilson, and Steve Zieser do **not** appear — consistent with the
  license page's artwork carve-out, and why no page carrying their work is
  committed. Editors credited by forum handle ("Zoso", "orbitalair") are not on
  the list; the license grant relied on here is the copyright holder's own
  statement on the PDF's license page.
