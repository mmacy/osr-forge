# A first conversion

Three commands take a module PDF to a draft adventure: price it, convert it,
tour the artifacts.

## 1. Estimate

`estimate` preprocesses the PDF (renders pages, extracts text layers) and
prices the conversion with pinned heuristics — no model call, no spend:

```sh
$ osrforge estimate my-module.pdf
pages: 48
page tokens: 27000 text + 43440 image
survey:   in=72440 out=3360
content:  in=88050 out=26400
monsters: in=5000 out=500
total:    in=165490 out=30260
estimated cost: $0.87
```

The estimate is deliberately rough (±40% on input tokens against the measured
calibration runs); the band, not the point value, is the contract. The workdir
it creates is warm — `convert` reuses the rendered pages.

## 2. Convert

Configure the provider ([provider setup](../guides/provider-setup.md)), then:

```sh
$ osrforge convert my-module.pdf
preprocess: running
preprocess: completed
survey: running
survey: completed (in=71203 out=2988)
content: running
content: completed (in=83102 out=21444)
monsters: running
monsters: completed (in=4305 out=340)
assemble: running
assemble: completed
validation: passed
wrote my-module.forge/adventure.json
wrote my-module.forge/overrides.yaml (commented template)
```

The pipeline is `preprocess → survey → content → monsters → assemble`. A
failure stops there, keeps everything upstream, and `osrforge rerun <stage>`
resumes — see [settings and rerun](../guides/settings-and-rerun.md).

## 3. Tour the artifacts

Everything lands in `my-module.forge/` — the
[workdir](../reference/workdir-artifacts.md):

- `adventure.json` — the draft, loadable by osrlib and anything built on it.
- `report.json` — what the pipeline was and wasn't sure about: per-area
  confidence, source pages, and [flags](../reference/vocabulary.md).
- `previews/*.svg` — one synthesized grid map per level, for eyeballing
  against the printed cartography.
- `overrides.yaml` — a commented template, waiting for corrections.

A conversion produces a *draft* — the geometry is synthesized from the
connection graph, not read off the map, and every gap and guess is flagged.
The [correction loop](../guides/correction-loop.md) is how a draft becomes
publishable.

## The same three steps as a library

```python
from pathlib import Path

from osrforge import ConversionSettings, convert
from osrforge.providers.foundry import FoundryProvider, FoundrySettings

provider = FoundryProvider(FoundrySettings.from_env())
result = convert(Path("my-module.pdf"), Path("my-module.forge"), provider, ConversionSettings())
print(result.report.validation.passed)
```

[`convert`][osrforge.convert.convert] also takes an `on_progress` callback
that receives each stage transition with its token usage — the stream a host
app's worker records.
