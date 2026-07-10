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
