# The answer-key classifier

An answer key for a printed dungeon map is built from two independent readings of the map and settled where they disagree (see `docs/plans/map-tracing.md` in re-pod, "How the work is checked"). The first reading comes from Claude. This tool gives the second: it reads every cell and every edge of a map from the ink alone, with no model of any kind, and writes the reading in the truth format of `osrforge.tracing.truth`. It is not the tracing stage's own ink measurement, so an answer key stays independent of the stage it scores.

The tool has four parts:

- `gridfit.py` fits the printed grid of a map region with numpy alone: one affine grid over the region, with a separate pitch and shear on each axis.
- `classify.py` measures each cell and each edge on that grid and decides its letter and a confidence. `styles.py` holds each style's thresholds, and `ink.py` turns the image into masks of ink, paper, and fill.
- `run.py` reads the maps a spec file names and writes one reading file per map. `reading.py` is that file's format.
- `score.py` scores a reading against a settled window, and `settle.py` helps a person settle one.

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

A *settled window* is a 12 by 12 window of one map whose cells and edges a person settled from zoomed crops. The format is in `score.py`. The Chaotic Caves windows this work settled are in `settled/`, and the crops they point at are under `~/.re-pod/map-plan/osr-suite/classifier/crops/`.

```text
uv run python -m tools.answer_keys.score <readings dir> tools/answer_keys/settled --diffs
```

The scorer follows the plan's scorer: cells right; edges right as passable or blocked, over the settled edges that touch floor or water; walls read as open, counted on their own; doors and secret doors found; the same with partly floor cells and the edges touching them left out; and the same with cells of outdoor ground left out.

To settle a new window, `settle.py` crops it with the grid drawn on (`crop`), fills a sketch from the classifier's reading (`from-reading`), expands a sketch into a settled window (`expand`), and lists where the reading and the settled window differ, with a zoomed crop of each item (`diff`). The sketch's `checked` map records each item the person looked at with its crop's path, and `notes` says how the window was settled.

## The grid fit

The fit takes the ink mask of the region and keeps the thin runs: ink no wider than 4 px across and at least 12 px long. For each axis it projects those pixels at a range of shears and keeps the shear whose projection is sharpest, estimates the pitch from the projection's autocorrelation and refines it on far peaks, finds each line as a peak of the projection, and fits position against line index by least squares, dropping lines more than a fifth of a pitch from the fit. A peak weaker than three tenths of the median peak is dropped first: it is a wall or a label off the grid, not a grid line.

The fit reports, per axis, the pitch, the shear, how many lines it found and kept, the root mean square and largest residual in pixels and in cells, and the share of lines within a tenth of a cell. The region's grid spans the rectangle the spec names.

On the seven regions this work fitted (the five Chaotic Caves regions and the two Palace levels), the rms residual is under 0.04 cells and the largest residual under 0.1 cells, and every detected line lies within a tenth of a cell of the fit. The shear is zero on the Chaotic Caves maps, which are digital drawings, and up to 0.005 on the Palace scans.

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

## Tests

`tests/` runs on the committed Chaotic Caves images: each map's grid fit meets the figures above, each reading loads through the truth models, and each reading scores at least the floor named in the test against its settled window. `uv run pytest` runs them with the rest of osr-forge's tests.
