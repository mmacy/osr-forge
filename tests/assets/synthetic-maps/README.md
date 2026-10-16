# Synthetic maps

Each directory is a map that `osrforge.tracing.synthetic` draws from a known level, so its answer is exact. A map's directory holds its answer (`geometry.json`), its manifest (`synthetic.json`), its page images (`images/`), and the survey, content, and map-reading caches the stage would have (`stages/`).

The generator writes every file here, so don't edit them by hand. Regenerate the set instead:

```
uv run python -m osrforge.tracing.synthetic tests/assets/synthetic-maps
```

A test regenerates the set and fails on any difference. The maps are CC0.
