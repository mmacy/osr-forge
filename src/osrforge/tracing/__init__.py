"""The tracing stage: a level's geometry read from its printed map.

The modules here are the parts that every later step of the map-tracing work
measures with. [`truth`][osrforge.tracing.truth] is the format that answer keys,
synthetic answers, and traces are written in. [`fivefoot`][osrforge.tracing.fivefoot]
turns a 5-foot reading into 10-foot cells. [`score`][osrforge.tracing.score]
compares a trace with its answer key. [`synthetic`][osrforge.tracing.synthetic]
draws the synthetic maps whose answers are exact, and
[`overlay`][osrforge.tracing.overlay] draws a reading over its map image. The
rules they share are written in `docs/tracing-conventions.md`.
"""
