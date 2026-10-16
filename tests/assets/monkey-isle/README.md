# JN2 The Monkey Isle

*JN2 The Monkey Isle*, Release 22, copyright © 2009-2015, 2018-2024 J.D. Neal, is a [Basic Fantasy Role-Playing Game](https://www.basicfantasy.org) adventure. It's the held-out member of the eval corpus (`tools/eval/corpus/jn2-monkey-isle/`). This directory contains its map images, for the tests of the stage that traces levels from printed maps.

## License and what is committed

The module's own license page (page 2 of the PDF) states:

> All textual materials in this document, as well as all maps, floorplans, diagrams, charts, and forms included herein, are distributed under the terms of the Creative Commons Attribution-ShareAlike 4.0 International License. Most other artwork presented is property of the original artist and is used with permission. Note that you may not publish or otherwise distribute this work as is without permission of the original artists; you must remove all non-licensed artwork before doing so.

Because the as-published PDF contains that non-licensed artwork, the PDF isn't committed. This directory contains only maps, which the license page places under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/):

- `maps/`: the module's map images. See [Map images](#map-images).

Everything in this directory derived from the module is distributed under CC BY-SA 4.0 with attribution to J.D. Neal and the Basic Fantasy Project.

## Map images

Each image in `maps/` is a map the PDF embeds, extracted with pypdfium2 at its native resolution and saved as PNG without changes to its pixels. `maps/maps.json` lists each image's PDF page, size, and sha256, and the levels of the corpus truth (`tools/eval/corpus/jn2-monkey-isle/truth.yaml`) it draws.

| File | PDF page | Printed caption | Levels | Size in pixels |
| --- | --- | --- | --- | --- |
| `p039.png` | 39 | Map for R1: HUMAN SACRIFICE | Human Sacrifice 1 | 1668 × 2078 |
| `p040.png` | 40 | Map for R2: REED MARSH TEMPLE | Reed Marsh Temple 1 (ground level) and 2 (top floor) | 1590 × 2436 |
| `p041.png` | 41 | Map for R3: LAKESIDE TEMPLE | Lake Side Temple 1 | 2025 × 1850 |
| `p042.png` | 42 | Map for R4: TEMPLE COMPLEX WITH SIMIANS | Temple Complex with Simians 1 | 1608 × 1680 |
| `p043.png` | 43 | Map For R5: UNDERWATER EXPLORATIONS | Underwater Exploration 1 | 1953 × 2356 |
| `p044.png` | 44 | Map for R6: Dragon Harbor | Dragon Harbor 1 | 1800 × 2229 |

The module's printed page numbers run two lower than the PDF's page numbers. The truth file cites printed page numbers, so its "page 37" is PDF page 39.

Each image was viewed in full and zoomed to confirm it contains only the map and its own legend, scale, and compass, with no non-licensed artwork. The small drawings in the Reed Marsh Temple's mud bog (area 6 on page 40) are part of the map. They draw the bones, skeleton, weapons, and chariot wheel that the area's key describes.

Pages 35 to 38 also print maps: the island's main map and players' map, a sheet of random lairs, and a common village. None of them draws a level in the corpus truth, so they aren't committed.

## The source PDF

To check the committed images against a fresh extraction, place the exact source PDF in this directory (it's gitignored). `tests/test_map_images.py` then compares every image in `maps/` with the image in the PDF, pixel for pixel.

- File: `JN2-Monkey-Isle-r22.pdf` (54 pages, 14,785,835 bytes)
- sha256: `6b4223b6fe2a93e530d7acc729c8c8604cd31d0c135be30558dd57096d737b84`
- Source: https://www.basicfantasy.org (Adventure Modules, JN2 The Monkey Isle, Release 22). The same PDF is archived at https://web.archive.org/web/20240521211130/https://basicfantasy.org/downloads/JN2-Monkey-Isle-r22.pdf.

## License verification record

- The PDF's license page states CC BY-SA 4.0 for all textual materials, maps, floorplans, diagrams, charts, and forms (quoted above), with the artwork carve-out that keeps the as-published PDF out of this repository.
- The license page credits the artwork to Cameron Dominick (cover), J.D. Neal, Alexander Cook, John Fredericks, Cory "Shonuff" Gelnett, and Kevin Morton. No page that contains their artwork is committed.
- The corpus manifest (`tools/eval/corpus/jn2-monkey-isle/manifest.yaml`) records that the sole copyright holder, J.D. Neal, appears on the Basic Fantasy Project's [contributor consent list](https://www.basicfantasy.org/consent-list.html). The license grant relied on here is the copyright holder's own statement on the license page.
