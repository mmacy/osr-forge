# Licensing

Package code is MIT. osr-forge ships no game content — the wheel and sdist
carry the `osrforge` package alone, and the release pipeline machine-checks
that neither artifact contains any test asset, tool, fixture, or PDF.

## Where the game content lives

- osrlib's SRD data (Open Game Content under OGL 1.0a) stays in osrlib —
  osr-forge depends on it and never copies it.
- The repository's test assets (`tests/assets/`) and eval corpus
  (`tools/eval/corpus/`) document their provenance and license in place: an
  original CC0 mini-module authored for the repo, and structural facts derived
  from CC BY-SA 4.0 Basic Fantasy modules, attributed to J.D. Neal and the
  Basic Fantasy Project. None of it is packaged.
- The eval corpus references its modules by pointer + sha256, never by bundled
  PDF.

## Map images

The repository includes the map images of two [Basic Fantasy Role-Playing Game](https://www.basicfantasy.org) adventure modules, as test assets for the stage that traces dungeon levels from printed maps. Each module's license page places its maps under the [Creative Commons Attribution-ShareAlike 4.0 International License](https://creativecommons.org/licenses/by-sa/4.0/) (CC BY-SA 4.0).

| Module | Release | Author | License | Map images |
| --- | --- | --- | --- | --- |
| *JN1 The Chaotic Caves* | Release 28 | J.D. Neal | CC BY-SA 4.0 | `tests/assets/chaotic-caves/maps/` |
| *JN2 The Monkey Isle* | Release 22 | J.D. Neal | CC BY-SA 4.0 | `tests/assets/monkey-isle/maps/` |

Both modules are copyright © 2009-2015, 2018-2024 J.D. Neal and published by the Basic Fantasy Project. The map images are distributed under CC BY-SA 4.0 with attribution to J.D. Neal and the Basic Fantasy Project.

Each image is a map the module's PDF embeds, extracted without changes to its pixels and saved as PNG. The `maps.json` file in each directory lists the PDF page each image comes from.

Other artwork in the modules belongs to its artists and isn't licensed. That's why neither PDF is committed, and why each map image was checked to contain only the map, its legend, its scale, and its compass before it was committed. Each module's README in `tests/assets/` records its source and license.

## What the fences do and don't bind

The fences bind the repository and the distributed artifacts, not users:
converting a privately owned, non-redistributable module locally is the
primary use case and must never be constrained by them. The design corollary:
no pipeline feature may persist module text outside the user's workdir —
anything that does (fixture recording) stays opt-in, developer-facing, and out
of the conversion path.

Conversion runs locally and everything derived from your module stays in your
own workdir — nothing is shared unless you share it. The one external party in
the loop is the model provider you configure, which receives page text and
images as extraction requests under your own account and terms.
