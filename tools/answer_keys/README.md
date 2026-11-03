# The answer-key classifier

An answer key for a printed dungeon map is built from two independent readings of the map and settled where they disagree (see `docs/plans/map-tracing.md` in re-pod, "How the work is checked"). The first reading comes from Claude. This tool gives the second: it reads every cell and every edge of a map from the ink alone, with no model of any kind, and writes the reading in the truth format of `osrforge.tracing.truth`. It is not the tracing stage's own ink measurement, so an answer key stays independent of the stage it scores.

The tool has six parts:

- `gridfit.py` fits the printed grid of a map region with numpy alone: one affine grid over the region, with a separate pitch and shear on each axis.
- `classify.py` measures each cell and each edge on that grid and decides its letter and a confidence. `styles.py` holds each style's thresholds, and `ink.py` turns the image into masks of ink, paper, and fill.
- `run.py` reads the maps a spec file names and writes one reading file per map. `reading.py` is that file's format.
- `score.py` scores a reading against a settled window, and `settle.py` helps a person settle one.
- `resolve.py` is the reference resolution script. It turns a reading into levels and areas by the conventions, and `world.py` is the grid it walks.
- `pipeline.py` builds an answer key from the two readings, Clef's answers, the settler, and the adversarial pass, stage by stage (see Build an answer key).

## Run it

From the osr-forge checkout:

```text
uv run python -m tools.answer_keys.run tools/answer_keys/maps.json --out <dir>
```

`maps.json` lists the committed Chaotic Caves maps. Each entry names the image, its style, the gray levels of its materials, the region to fit in pixels, the range of grid pitches to consider, and what the truth format records about the region: its scale, compass, and legend. Specs for commercial maps, like Palace of the Silver Princess, stay outside the repository; the ones this work used are in `~/.re-pod/map-plan/osr-suite/classifier/specs/palace.json`.

Each map gets `<dir>/<id>.json`, a reading file with:

- `region`: a truth-format `Region`, with the registration (every grid corner's pixel position)
- `panel`: a truth-format `Panel`, with the cells, the edges, the partly floor cells, and the cave cells
- `confidence`: a number for every cell and edge letter, from 0.5 (a coin flip) to 1
- `fit`: the grid fit's figures

`load_reading` in `reading.py` reads one back and validates `region` and `panel` through the truth models.

To see what a reading says, add `--overlay x0,y0` to draw a 12 by 12 window of it over the map: floor cells tinted green, walls red, doors blue, secret doors magenta, windows cyan, printed open lines yellow, and partly floor cells with an orange dot.

## Score it

A *settled window* is a 12 by 12 window of one map whose cells and edges a person settled from zoomed crops. The format is in `score.py`. The Chaotic Caves windows this work settled are in `settled/`, and the crops they point at are under `~/.re-pod/map-plan/osr-suite/classifier/crops/`. A window's `checked` paths are relative to `~/.re-pod/map-plan/osr-suite/classifier/settled/`, where the windows were settled, so they don't resolve from the repository.

```text
uv run python -m tools.answer_keys.score <readings dir> tools/answer_keys/settled --diffs
```

The scorer follows the plan's scorer: cells right; edges right as passable or blocked, over the settled edges that touch floor or water; walls read as open, counted on their own; doors and secret doors found; the same with partly floor cells and the edges touching them left out; and the same with cells of outdoor ground left out.

To settle a new window, `settle.py` crops it with the grid drawn on (`crop`), fills a sketch from the classifier's reading (`from-reading`), expands a sketch into a settled window (`expand`), and lists where the reading and the settled window differ, with a zoomed crop of each item (`diff`). The sketch's `checked` map records each item the person looked at with its crop's path, and `notes` says how the window was settled.

## Resolve a reading

An answer key records a settled reading: cells, edges, key marks, outlines, and symbols. Its areas, ownership, cuts between levels, entrances, and links follow from that reading by the rules in `docs/tracing-conventions.md`. `resolve.py` applies those rules and nothing else, so the resolution layer of every answer key comes from the same text the tracing stage's resolution function follows.

`resolve(reading, survey, contents, mapread)` takes a geometry truth document, reads its pages, regions, panels, and half pairs, and returns the document with each dungeon's levels and entrance filled in, in canonical form. It needs the map's survey cache, its content caches by level, and its map-reading cache, which `load_caches` reads from a map directory's `stages/`. The rules run in the order the conventions give: cells and edges, the 5-foot conversion, the first partition, key placement, the final partition, areas and doors, panel placement, cuts and passages, entrances and links. Checks and repairs are the tracing stage's, so the script makes none.

To compare the script's result with a committed synthetic answer:

```text
uv run python -m tools.answer_keys.resolve tests/assets/synthetic-maps/manor
```

It prints `exact`, or the fields of each level that differ and the cells behind each difference. `world.py` holds what the rules walk: every panel's cells and edges on one graph, the fold links between halves that stay apart, open spaces, open ground, walled rooms and structures, narrow runs, one-cell necks, and the walks that measure distances.

Where the conventions give no answer for an input, the script raises `ConventionGap` with the case, so the gap is settled in the conventions and not in the code.

## Build an answer key

`pipeline.py` runs the answer-key method of the plan over the maps a key spec names: `keys.json` here covers the committed Chaotic Caves maps and the two synthetic maps the pipeline is checked on, and the Palace spec stays outside the repository. Each map's requests, answers, crops, and calls go under the spec's `work` directory, in `<work>/<map id>/`, and the classifier's readings in `<work>/classifier/`. From the osr-forge checkout, after `run.py` has written the readings:

```text
uv run python -m tools.answer_keys.pipeline tools/answer_keys/keys.json <map id>... <stage> [submit|collect] [--wait]
```

The stages, in order:

1. `windows submit` and `windows collect`: the first reading. `tiles.py` covers the region with 12 by 12 windows and draws each with red ticks and grid numbers in a white gutter; `reader.py` asks Claude Sonnet 5.5 at high effort for every cell and edge of the window in the truth letters, with the labels and symbols printed in it, as JSON the API checks against a schema. `windows.py` checks what the schema can't (the number of rows, each row's length, the letters, and that every named cell lies in the window) and asks once more for a malformed answer, with the answer and its faults appended; a window whose retry fails too gets a fresh attempt. Every request goes through the Batches API (`batches.py`), which reserves each request's worst case on the shared Claude ledger before sending and settles it at half the list price when the answer comes back.
2. `marks submit` and `marks collect`: the second reading of labels and symbols (`marks.py`), on windows laid half a window across and down from the first tiling, at medium effort, asking for labels and symbols alone.
3. `wholemap submit` and `wholemap collect`: the whole page once, with the region's grid ticked, for each level's outline rectangles, seed cells, and open ground, the legend's entries from the symbol vocabulary, the compass, the scale, and the edges that cut the drawing off.
4. `clef`: Clef's answer for every edge that touches floor or water in either reading, and for every cell the two readings dispute (`clefpass.py`), through `osrforge.providers.clef.ClefProvider` and the Clef ledger. The crops are the probe's best setup: 2 by 2 cells at 128 pixels per cell with a red box on the edge, and the options and their descriptions depend only on the style. `clef export` writes the same crops and questions to `<map>/clef/requests/` for a reader outside this environment, such as the maintainer's local Clef through Ollama; its answers go to `<map>/clef/answers.jsonl` in the same form, and the pass skips items that already have one.
5. `disagree`: the table of every item's readings, `items.json` (`readings.py`). A cell is disputed when Claude's windows differ or the classifier's letter isn't among Claude's; an edge when any two letters differ, or when Clef, with a top probability of at least 0.5, is passable where the letters are blocked or the other way round, or names a different kind of door; a label when the two readings place it in different cells or one lacks it; a symbol likewise.
6. `settle submit` and `settle collect`, run three times: the first round settles the cells, labels, and symbols, the second the edges, with the settled cells applied so an edge beside a cell settled as rock is a wall without a question unless a reader read a door there, and the third the outlines (`letters.py`): each open edge that leads from a level's outline onto floor no outline covers, and each open-ground rectangle of the whole-map read, confirmed as open ground of its level or sent to the outside. Each request (`settler.py`) carries up to eight items, each with a zoomed crop and a wider crop with the item boxed in red and the readings that disagree, and is fresh: it carries none of the earlier work beyond those readings.
7. `adversary submit` and `adversary collect`, run twice: the first round rechecks every settled call and the agreed cells and edges in caves, at diagonals, and around keys, in fresh requests that carry the recorded value and try to prove it wrong; the calls within one cell of every cave mouth a reading placed, and the agreed mouths themselves, are always among them and go out first, because an entrance letter printed in a mouth reads as rock; the second re-settles every challenged call in a fresh settling request that carries the challenge among the readings, and that result is final. A submit that finds its challenges already being re-settled sends the pass's next part instead. `final.json` holds every call's final value with the settler's and the adversary's beside it.
8. `assemble`: the map's `Region` and `Panel` from the table and the final values, with the outlines from the whole-map read, as a geometry truth document whose levels are shells (`assemble.py`, `<map>/reading.json`). An edge touching no floor is `-`, and an edge between floor and rock is a wall unless the settler put a door there. The log beside it counts the cells and edges that fell back to the classifier because no window read them, lists each outline border that crosses floor, and lists each cave mouth whose edge isn't passable or whose cells aren't both floor (`mouths_closed`), which can't make the ground beyond it the outside.
9. `combine`: the region documents of one member joined, resolved with the member's caches through `resolve.py`, and written as the key (`--stages` names the directory whose `stages/` holds the caches).
10. `sample`: the owner's sample of one style (`samples.py`), about 50 settled calls drawn from caves, diagonals, and keys, plus every label the settler placed, with the crops copied beside the sample file for the sample app.
11. `score`: on a synthetic map, the assembled reading against the exact answer through `osrforge.tracing.score.score_trace` and a direct comparison of the labels, symbols, and outlines; on a Chaotic Caves map, against the settled windows in `settled/` (`scoring.py`).
12. `recheck submit --sample <file>` and `recheck collect`, after the owner's verdicts: a `wrong` verdict sends every call of that category on that map back for a fresh settling round, in which the sampled call carries the owner's verdict and note; an `unsure` verdict sends that call alone with the note. A recheck's value is the call's final value from then on (`recheck.py`).

Every stage can be run again: jobs and answers that exist are kept, and `collect --wait` polls until every open batch has ended. On a map whose entry sets `slopes` (the Chaotic Caves main map, whose lairs' ground runs into the ravine across elevation lines), the settling round after the edges is the slope round: one request per level asks for the cells of the elevation line that bounds the level's ground inside its rectangle, and the level's outline then ends at that line, on its seeds' side, before the outline round asks about the borders that still cross floor, into another outline or into floor no outline covers. `settle submit --jobs N` and `adversary submit --jobs N` send at most N jobs and leave the rest pending for the next submit, so a large pass goes out in parts whose worst case fits under the budget line. The API key is read from `~/repos/re-pod/.env` inside the process, the Clef account id from the probe's client script, and the Clef token from the wrangler login; none is written anywhere.

## The grid fit

The fit takes the ink mask of the region and keeps the thin runs: ink no wider than 4 px across and at least 12 px long. For each axis it projects those pixels at a range of shears and keeps the shear whose projection is sharpest, estimates the pitch from the projection's autocorrelation and refines it on far peaks, finds each line as a peak of the projection, and fits position against line index by least squares, dropping lines more than a fifth of a pitch from the fit. A peak weaker than three tenths of the median peak is dropped first: it is a wall or a label off the grid, not a grid line.

The fit reports, per axis, the pitch, the shear, how many lines it found and kept, the root mean square and largest residual in pixels and in cells, and the share of lines within a tenth of a cell. The region's grid spans the rectangle the spec names.

On the seven regions this work fitted (the five Chaotic Caves regions and the two Palace levels), the rms residual is under 0.04 cells and the largest residual under 0.1 cells, and every detected line lies within a tenth of a cell of the fit. The shear is at most 0.0005 on the Chaotic Caves maps, which are digital drawings, and up to 0.005 on the Palace scans.

## The rules

Every style uses the same rules. A style sets the thresholds and switches the rules on or off, and a map's spec sets the gray levels that separate its materials.

### Cells

A cell is floor when at least half of it is floor, counted over the cell's inner 70 percent and among the pixels that are paper or fill, after a close that fills grid lines, text, and small symbols. Ink that is neither paper nor fill (a label, a pillar, a trap box) counts for neither side, so a symbol on floor leaves the cell floor. A cell between a quarter and three quarters floor goes in `partial`. On the cave and dark rock styles a cell between 8 and 92 percent floor goes in `cave` as well, because those styles draw their walls off the grid.

A cell that isn't floor is rock, or water on a map whose spec says its fill is water (the manor's moat): then fill reads as `~` and ink as `#`.

On Palace level 2, whose outside is bare paper, a floor cell also needs a printed line on at least three of its four sides (`floor_needs_grid`), because the paper outside the building has no grid.

A cell with at least four (TSR blue) or six (the other styles) parallel bars across it is a stair. Edges across the bars' direction between stair cells read open unless a wall covers them.

### Edges

An edge touching no floor or water is `-`. An edge between floor and rock is a wall. Every other edge is measured on a patch that runs a quarter cell beyond each end of the edge and half a cell to each side:

1. The printed line is centred: the densest run of wall ink within a fifth of a cell of the fitted line, nearest the fitted line, is the line the edge is read on. On the black and white and cave styles, where a wall may be drawn up to half a cell off the grid, an edge with little ink on its own line takes a parallel wall within half a cell that reaches an end of the edge (`snap_offset_walls`). A wall exactly between two lines goes to the west or north line.
2. A door is a box: two short sides across the line, wider than the wall by 4 px and at least a quarter of a cell, between a fifth and the whole of the edge apart, with a paper interior and a long side beyond the interior on at least one side of the line. Ink inside the box marks the door: an S makes it secret (`s`) on the TSR styles, a dot makes it locked (`l`) on the cave style, as page 38's legend says. A box whose sides are closer to the line than 0.12 cells is a window (`n`), and so is a round one on the black and white style, where windows are circles.
3. A gap in a thick wall with ink on the line at both ends, no box, and paper no farther than the wall's own half width is a window slit (`n`). On the TSR styles a gap barred at both ends with paper beyond is an archway (`p`).
4. A wall with ink reaching beyond the wall's width on both sides of the line somewhere along one run of the edge, the run between a fifth and nine tenths of the edge, clear of the edge's ends, and the wall continuing outside it, has a secret door's glyph (`s`).
5. Three or more thin dark runs on the line with paper runs between them are a dashed partition, a printed open line (`p`), on the styles that print one.
6. Otherwise the edge is blocked when the ink across the line, summed over 5 px each side and taken as the median along the edge's middle, reaches the style's `wall_min` (1.8 px on TSR blue, 3.5 px elsewhere), or when fill lies on the line along more than half the edge. Ink that covers less than 80 percent of the line counts less, and ink that touches neither end of the edge counts for nothing, because a wall runs through its edge and a label doesn't.

The confidence of a blocked or open edge comes from how far the ink measure sits from `wall_min`; a door, window, or glyph reading has a fixed confidence.

### Where it fails

- **Labels on a line.** A key or a letter printed across a grid line or against a wall is read as ink on that line. The result is a wall, a door box, a locked or secret door, or a dashed line where there is none. This is the largest source of errors on every style.
- **Doors centred on a grid corner.** A door box drawn where two grid lines cross belongs to no one edge, and the box detector, which looks for both ends of the box along one edge, misses it.
- **Thin walls the weight of a grid line.** On Palace level 1 some cave walls are drawn no heavier than the grid, and the ink measure can't tell them apart.
- **Diagonals and freehand passages.** A cell crossed by a diagonal wall or a freehand cave passage is settled by the half rule, and a passage that straddles a grid line can leave both cells under half. These cells are partly floor, and the scores without them say how the rest reads.
- **Outdoor ground.** Paper with a grid on it reads as floor, inside and outside a building alike. The resolution's outlines, not the reading, decide what is outside.

## Check a sample

An answer key is approved by checking a sample of its settled calls. The sample app shows one call at a time, with the zoomed crop and the wider crop, the member, map, panel, style, kind, category, and item, each reading, the settled value, the adversarial pass's value, and the settler's reason. From the osr-forge checkout:

```text
uv run python -m tools.answer_keys.sample_app <sample.json> [--port 8765]
```

It serves on `127.0.0.1` only and prints the address to open. `<sample.json>` is a sample file in the format of `sample.py`, with the crops it names beside it.

The keys are:

- `r`, `w`, and `u` record the call as right, wrong, or unsure, and move to the next call.
- The left and right arrow keys move between calls without recording anything.
- `n` opens a note. The note is recorded with the next verdict on that call. Press Enter or Escape to leave the note box, then press a verdict key.

The app starts at the first call with no verdict and shows how many calls have one. It writes the verdicts file beside the sample file, named from the sample file's stem with `.verdicts.json` (`sample.json` gives `sample.verdicts.json`), after every verdict, replacing the whole file, and reads it back when it starts, so you can stop and come back. If the sample changed after you gave those verdicts, so that a verdict names a call the sample lacks or judged another settled value, the app refuses to start rather than carry them over. A commercial map's sample, crops, and verdicts stay outside the repository.

## Tests

`tests/` runs on the committed Chaotic Caves images: each map's grid fit has an rms residual under 0.05 cells, a largest residual under 0.1 cells, and every detected line within a tenth of a cell, each reading loads through the truth models, and each reading scores at least the floor named in the test against its settled window. `test_resolve.py` resolves every committed synthetic map and compares the result with the map's answer in canonical form, with flags left out. `test_resolve_rules.py` resolves hand-made readings for the rules no committed map reaches and checks what the conventions say for each. `uv run pytest` runs them with the rest of osr-forge's tests.
