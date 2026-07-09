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
- `fixtures/` — recorded request/response fixtures from the capability spike
  (see `docs/foundry-capabilities.md`). Replay-grade fixtures pair with
  `pages/`; evidence-grade fixtures (image-count, DPI, and context boundary
  probes) are committed without their page assets and replay is not promised.

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
