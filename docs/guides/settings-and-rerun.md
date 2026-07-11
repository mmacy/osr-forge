# Settings and rerun

Pipeline behavior is a set of deterministic knobs on
[`ConversionSettings`][osrforge.settings.ConversionSettings], echoed into each
workdir's `run.json` so every stage reads the same configuration the run was
started with.

## The knobs

| Knob | Default | Meaning |
| --- | --- | --- |
| `render_dpi` | 150 | Page-render resolution (a legibility knob, not a cost knob — image tokens are DPI-independent) |
| `max_pages` | 200 | Source page-count guardrail |
| `max_source_bytes` | 100 MiB | Source file-size guardrail |
| `blank_page_renders` | `()` | Page numbers whose renders are emitted as blank white PNGs (text layer still extracted) — the content-safety-filter workaround; each blanked page is flagged `page_unreadable` |
| `content_batch_pages` | 8 | Content-pass batch size in pages (floor 2) |
| `survey_max_pages` | 50 | The survey chunk size — the service's measured 50-images-per-request cap: a source at or under this many pages surveys in one request; a larger source surveys in page windows of this size, merged before normalization |
| `monster_fuzzy_threshold` | 0.85 | Monster resolution's fuzzy-tier auto-accept floor, pinned against measured catalog pairs |
| `monster_llm_top_k` | 8 | Candidate templates offered per name in the monster-resolution LLM tier |
| `unresolved_fallback` | `best-effort` | Where resolution or parsing came up empty: flagged level-band monster stand-ins and unguarded-treasure rolls (`best-effort`), or leave the gap (`omit`) |

On the CLI, `--set KEY=VALUE` is the repeatable settings channel; values parse
as YAML, so `--set 'blank_page_renders=[21]'` and `--set
unresolved_fallback=omit` both coerce naturally.

## Rerun: resume any stage

`rerun` re-runs one named stage — *and everything downstream of it* — from
cached upstream outputs:

```sh
osrforge rerun assemble --workdir my-module.forge     # the correction loop's assemble
osrforge rerun monsters --workdir my-module.forge     # re-resolve, then re-assemble
osrforge rerun survey --workdir my-module.forge       # re-survey, then everything after
```

The stage argument *is* the skip: everything upstream is kept verbatim, and
each stage already clears or supersedes its downstream caches, so the workdir
stays artifact-consistent. `rerun assemble` needs no provider and is the
documented correction-loop step; `rerun preprocess` reads the workdir's own
`source.pdf`.

## The drift guard

Changing settings on an existing workdir goes through `rerun --set`:

```sh
osrforge rerun preprocess --set 'blank_page_renders=[21]' --workdir my-module.forge
osrforge rerun assemble --set unresolved_fallback=omit --workdir my-module.forge
```

Every knob has an owning stage, and a knob owned by a stage *upstream* of the
rerun stage is rejected with the stage to rerun instead:

```text
$ osrforge rerun assemble --set render_dpi=200 --workdir my-module.forge
osrforge: setting 'render_dpi' belongs to the preprocess stage, upstream of assemble — rerun preprocess instead
```

Without the guard, the `run.json` settings echo would claim pages were
rendered at a DPI they weren't — the echo is the single source of truth stages
read, and it is never allowed to lie about how upstream artifacts were
produced.
