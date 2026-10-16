# Map-tracing conventions

These rules turn the reading of a printed map into levels and areas. The synthetic map generator applies them when it writes a map's answer, the reference resolution script applies them when it derives an answer key's areas from its settled reading, and the tracing stage's resolution function applies them when it builds a level. Because all three follow this text, the scorer counts reading errors, not differences in taste. The models the rules read and write are in `osrforge.tracing.truth`, and this page uses their names.

A *reading* is what a map shows: each cell as floor, rock, or water, each edge as open, a printed open line, a wall, a window, a door, a locked door, or a secret door, and the key marks, outlines, symbols, legend, compass, and scale the whole-map reading reports. A *resolution* is what these rules make of it: each level's grid, areas, placed keys, transitions, and passages, and each dungeon's entrance.

## Coordinates and order

A grid's cell `(x, y)` is `x` columns from its west edge and `y` rows from its north edge, both 0-based. A cell's sides are north, east, south, and west, named for the grid, whatever the map's compass says. A panel's grid is the registered region's grid, or the grid two joined halves share. A level's grid is the grid its panels are placed on.

Distances in these rules are in 10-foot cells, after a 5-foot panel is converted. *Walking distance* between two floor cells is the fewest steps between them across passable edges and doors, one step per edge. *Within N cells* of a cell, for a cell that isn't floor, means a Chebyshev distance of at most N: the larger of the column difference and the row difference.

Every tie breaks by *north-then-west order*: the item farther north comes first, and of two equally far north, the one farther west. North is where the map's compass points on the page, and a map with no compass has north at the top. A region's compass sets north for its panel, two joined halves share the compass either prints, and a level's north is the north of its first panel. The order sorts by a point: a cell's centre `(x + 0.5, y + 0.5)`, or an edge's midpoint. With north at the top of the page the key is `(y, x)` ascending. With north at the left edge it is `(x, -y)`, with north at the bottom `(-y, -x)`, and with north at the right edge `(-x, y)`. `order_key` in `osrforge.tracing.truth` computes it. An area's *first cell* is its first cell in this order, and a level's *first key* is the first key in survey order.

The level grid isn't rotated to put north at the top. A level is drawn the way its page is, so a person can recognize the map in it, and the recorded north serves the rules that need it.

## The order

The rules run once, in this order:

1. Cells and edges.
2. For a 5-foot panel, the conversion to 10-foot cells.
3. A first partition of ownership, from the seed cells and the keys printed on floor.
4. Placement of the other keys: keys off the floor, on doors, and on water, and letters.
5. The final partition, from the seed cells and every placed key.
6. Areas, then doors.
7. Placement of each level's panels on the level's grid. The rules after this step address cells by their place on the level's grid, and each level gets edges for its own cells only.
8. Cuts between levels, with their passages, and the passages between halves that stay apart.
9. Entrances and links.
10. Checks, then repairs. After a repair the rules run again from step 3, at most three times. A level that hasn't settled by then is flagged in the report, and its remaining faults count as not repairable.

## Cells and edges

- A cell is floor when at least half of it is floor. A cell between a quarter and three quarters floor, or on a diagonal, is also recorded in the panel's `partial` list, so the scorer can leave it out.
- The cells of a diagonal run of floor stay joined edge to edge. Each stair step of the diagonal goes on the side with more floor.
- An edge is passable (open, or a printed open line) only when the probability of those two readings together passes the threshold set for its map style. Otherwise the edge takes its most probable reading among the others. Every cell, and every other edge, takes its most probable reading. In an answer key and a synthetic answer the reading is settled, so each edge has one letter.
- A window is a wall.
- A floor cell stays floor when a symbol is drawn in it, like a pillar, a statue, a pool, or steps that no link names.
- Lines drawn over the map (contours, trees, tracks, skylights, and stream hatching) are neither walls nor water.
- A part of the map drawn as neither floor nor water (a void, a shaft) is rock.
- Water that has no key is impassable. A bridge, or footings on both banks facing each other across the water, makes the water cells between them a *crossing*. A crossing is walkable and its cells are floor in the resolved level.
- A cell whose walls are drawn off the grid is recorded in the panel's `cave` list. An edge with a cave cell on either side is a cave edge.
- An edge has a reading only where floor or water lies on at least one side. Every other edge is `-`.

## 5-foot maps

A 5-foot panel becomes 10-foot cells before any other rule runs. The *block phase* `(ox, oy)`, each 0 or 1, chooses which pairs of squares become one cell: the 10-foot lines across the panel lie on the 5-foot lines `ox + 2k`, and the 10-foot lines down it on `oy + 2k`. Cell `(X, Y)` covers squares `2X - ox` and `2X - ox + 1` across and `2Y - oy` and `2Y - oy + 1` down. Squares outside the 5-foot grid count as rock, so the converted panel is `ceil((width + ox) / 2)` cells wide and `ceil((height + oy) / 2)` high.

The conversion uses the phase that puts the most blocked 5-foot segments (wall, window, door, locked door, secret door) on 10-foot lines. On a tie it uses the phase with `oy = 0`, a 10-foot line on the panel's northernmost grid line, and then the one with `ox = 0`, a 10-foot line on its westernmost grid line.

A cell is floor when at least two of its four squares are floor, otherwise water when at least two are water, and otherwise rock. A cell is partly floor when its squares aren't all the same kind or any of them is partly floor. A cell is a cave cell when any of its squares is.

A 10-foot edge between two cells covers two 5-foot segments. Its letter is the first of the two in this order: `l`, `s`, `d`, `n`, `w`, `p`, `o`. A `-` segment is skipped. An edge whose segments are both `-` but that touches floor or water is `w`. So an edge is passable only when both of its segments are, and a door on either segment makes the edge a door.

A *narrow run* (see Keys and areas) stays joined edge to edge even where it's one square wide: every cell that covers a square of a narrow run is floor, and the edge between two consecutive cells of the run is passable.

A wall or door segment between two floor squares on a line that isn't a 10-foot line moves to a 10-foot line. Segments move in *runs*: a run is a maximal straight sequence of blocked segments between floor squares on one line. These rules decide which way a run moves, in order:

1. Every walled room is left with at least one cell.
2. Every narrow run stays joined.
3. The run moves toward the larger of the two parts it separates, counted in squares of the open spaces on its two sides.
4. Otherwise, a run on a line that runs north and south moves east, and a run on a line that runs east and west moves south.

When a run moves, the squares between its old line and its new line go to the side the run moved away from, the run's segments become open, and the segments on the new line along the run take the run's letters, the letter of each moved segment going to the segment beside it. The conversion leaves a wall between two walled rooms that it would otherwise join: after the moves, a 10-foot edge between cells of two different walled rooms of the 5-foot map is `w` when the rules above would make it passable. Each move is recorded with its line, where it went, and its extent, and the report flags it.

Marks, symbols, seeds, and outline rectangles convert to the cells that contain their squares. A rectangle becomes the smallest rectangle of cells that covers it.

An answer key for a 5-foot map records 5-foot squares, and the scorer converts it with this rule. Scores against the answer keys then measure the reading, and the synthetic maps test the conversion.

## Who owns a cell

Which level owns a cell is a separate question from whether a party can walk on it.

- Floor and water inside one level's outline belong to that level, including open ground. Floor and water inside several outlines are divided among those levels by the *partition*. Floor and water outside every outline are part of the *outside*. A party can't walk there, and repairs never change it.
- The partition grows from the seed cells and the placed keys at the same time, as the cheapest walk from any source. A cell goes to the level of the source that reaches it most cheaply. Of two sources at the same cost, a key beats a seed, and of two sources of the same kind, the one whose cell is first in north-then-west order wins. The costs are:

  - an ordinary step across a passable edge: 1
  - a step onto a cell of a one-cell neck: 2
  - a step across a door of any kind, or across a cave mouth: 4
  - a step across a wall or window: 20
  - a step onto a rock cell: 20, so floor that's cut off still gets an owner.

  A *one-cell neck* is a floor cell with floor on two opposite sides and no floor on the other two.

- The rules count as open ground the floor inside the whole-map reading's open-ground rectangles that lies outside every walled structure.
- Floor that a walk from a level's keys reaches only through one of the level's cave mouths is part of the outside, even inside the level's outline, unless it's open ground.
- An *outer opening* is any of these:

  - a passable edge, door, or cave mouth between floor that isn't open ground and either the outside or open ground
  - a passable edge between open ground and the outside
  - floor that runs off the map's frame (a passable frame edge beside a floor cell), but not off a half's edge at the fold
  - any cave mouth, whatever lies beyond it.

  The rules find outer openings from the drawing, before ownership. A door that the rules synthesize is never an outer opening.

- Two levels whose floors meet are separated by a *cut between levels*. The cut runs where the two levels meet in the partition, through corridor cells only, so no area is split, except for the one cell a vestibule takes. Where the two levels' areas meet across an open edge with no corridor, the cut is that edge.
- Every edge a cut crosses is a wall on both levels, and passages go only where the party could cross. A passage's *far cell* is the cell one step across its side. The passages come in pairs, made in this order:

  1. The vestibules and their pairs, in north-then-west order of the vestibules' door edges.
  2. A pair for each other passable edge the cut crosses, in north-then-west order of the edges. One passage stands on the cell each side of the edge, each leading to the other cell and facing the way the party crosses.

  A pair that would share a far cell with a pair already kept is dropped, so a passage and its partner are always kept or dropped together. Where one level's floor reaches a single cell of the other level from two places, only one pair can use that cell, and the checks flag any floor left unreachable.

- A cut never runs along a door. Instead, the door gets a *vestibule*: the cell beyond the door on the side of the level later in survey order. The vestibule becomes a corridor cell of the earlier level, and rock in the later level, so the door has floor on both sides.
- The vestibule has one pair of passages, across a side that qualifies. A side qualifies when it's a passable edge to the later level's floor and its pair would share no far cell with a pair already kept. A qualifying side is *free* when no pair across another passable edge or door of the cut would use its far cells, not counting the vestibule's own sides. The pair goes on a free side if there is one. Among the free sides, or among all qualifying sides when none is free, it goes on the side opposite the door, or else on the first in north-then-west order. The vestibule's other sides are walls on both levels, with no passages.
- The vestibule is refused when no side qualifies, when taking it out of the later level would split that level's floor or leave an area empty or in two parts, when it has another door, or when a link's symbol is in it. Then the vestibule is tried on the other side of the door, with the two levels' roles swapped. Where neither side works, the door is dropped, and the cut runs along its edge with a pair of passages. The report flags the door's lock, stuck state, or secret kind as lost.
- The report flags each pair, each vestibule, and each dropped door or pair. When a pair of passages makes a prose connection, that connection isn't a dispute.
- The report flags every part of an outline's border that crosses floor, and the stage sends it to Claude as an uncertain item: which side of it belongs to the level. The outline rectangle with that border is moved or split to match Claude's answer, and the rules run again. The outlines in answer keys are settled by the same rule, so an answer key's outline borders follow walls, doors, cave mouths, printed lines, or the frame.

## Keys and areas

Where two of these rules claim the same cells, the earlier one wins.

An *open space* is a maximal set of floor cells joined across edges that aren't walls, windows, or doors. A *walled room* is an open space every one of whose boundary edges is a wall, window, or door, and that contains no open ground. A *walled structure* is a walled room or a group of walled rooms joined by doors with no corridor between them.

A *narrow run* is a maximal chain of floor cells, each with at most two passable floor neighbours and no part in a 2 by 2 block of floor cells joined across passable edges, where consecutive cells are joined by a passable edge that isn't a printed open line. A junction, a door, a printed line, or a change of width ends it. A bend doesn't.

- A whole label is matched against the survey's keys before a trailing letter is read as a suffix, so a printed 6a belongs to a key named 6a, if the survey has one.
- A key *names* a letter only when that key's description quotes the letter ("a"), attaches it to the key's number (41a), or puts it after "cell", "area", "marked", or "at". Case is ignored, except that a and i never count after "at", so "at a" names nothing. If one key in the letter's level names it, the letter joins that key. If several do, it joins the nearest of them by walking distance, and the report flags it. A letter on the outside, a letter that matches its dungeon's name, and a letter that no key names are only labels. A letter printed as a suffix (38a) joins its number's key.
- A key printed on floor stays where it's printed. A key printed on the outside, within two cells of a cave mouth or outer opening of its own survey level, moves to the floor cell just inside the nearest one. A key printed on rock or on an outer door (a door that is an outer opening) moves to the nearest floor cell of its own survey level that a walk from it reaches without crossing a cave mouth or an outer opening. Any other key printed on the outside moves to the nearest floor cell of its own survey level. A key printed on an inner door goes to the side of the door with no other key. When both sides or neither side has another key, it goes to the side whose cell comes first in north-then-west order. The report flags each move, and the placed key's `moved` is true.
- A key with a leader line belongs where the line ends.
- A survey key that the map doesn't print, and that is the map-reading stage's `entrance_key` or is titled "Entrance" or "Entranceway" (the first two sources in Entrances), takes the unkeyed floor that one outer opening leads into. That opening is the outer opening nearest the area that the key's prose connects it to. The key takes floor up to the first door, keyed area, or unlabelled narrow run of three or more cells.
- Any other survey key that the map doesn't print takes the unlabelled walled room beside the area of its own level that its prose connects it to, behind the door the prose names if there is one. With no such room, the key becomes a *synthesized area*: a 2 by 2 block of floor on rock beside that area, joined to it by a synthesized door, in the first position in north-then-west order where the block fits with one cell of rock around it. A key whose prose connects it to no area of its own level becomes a synthesized area beside its level's first area. The report flags each, and the area's `synthesized` is true.
- A key printed in two places gets one area with both places.
- A key printed in a walled room gets the room's cells. A walled room with several keys is divided as an open space is.
- A narrow run with a label in it belongs to that label's key. An unlabelled narrow run of three or more cells stays corridor.
- A key on open ground gets the drawn structure it's printed in, or a structure beside it that has no other key. Otherwise it gets the cells within two cells of its label, divided with any other keys there by walking distance.
- Any other open space with one key is that key's area. An open space with several keys is divided among them by walking distance, with printed open lines counted as walls for the walk. A cell at the same distance from two keys goes to the key whose placed cell is first in north-then-west order. A printed open line bounds areas only between two keyed parts.
- A key printed on water takes the water cells within three cells of its label, divided with any other keys there as in an open space, and those cells become walkable floor. The report flags it.
- An unlabelled walled room, or an unkeyed part of an open space closed off by printed open lines, joins the keyed area it opens onto, through its door or its printed line, and of several the first door or line in north-then-west order. A row of unlabelled rooms beside a keyed room of the same shape joins that key.
- A walled structure that has a key and no door gets a synthesized door, which the report flags. The door goes on the structure's wall nearest, by walking distance outside the structure, to the level's first key in survey order, and of several equally near walls on the first in north-then-west order.
- Floor outside every area is corridor.

## Doors

A door's kind comes from the map. A door is locked when the prose says so, or when the reading gives a locked door there and the legend in force has the locked-door symbol. The legend in force is the page's own, or the one the page uses from another page. Where that legend has no locked-door symbol, a door read as locked counts as a plain door. A lock that the prose states goes on one door: of the doors on the shortest walk between the two areas that the prose names, the one nearest the first area. Equally short walks are compared cell by cell in north-then-west order. With no door on that path, the lock is dropped and the report flags it. Whether a door is stuck comes from the prose.

## Panels and the level's grid

A panel is one registered region, or the two halves of a map joined into one. A level can be drawn on several panels with no floor between them: two tiers of a pyramid, the halves of a map that stay apart, or an inset. Each panel has its own cells, after a 5-foot panel's conversion, and links or passages join the panels.

On a level's grid, the panels go west to east in page order. Panels on one page go in the order of their frames' top edges on the page, highest first, and frames whose top edges are level go left to right. Panels are two cells of rock apart, so the far cells of two facing passages never coincide. Panels are aligned at the top, except that two halves left apart are aligned by the row shift their half pair records. Placing a panel doesn't change its layout. After the cuts between levels, the level's grid is shifted so that its floor has one cell of rock on every side. That puts every passage's far cell on the grid. The level's `panels` record where each panel's cell `(0, 0)` sits.

A cell of a level's grid that the level doesn't own is rock in the level's cells, and an edge that touches none of the level's floor or water is `-`.

### Halves that stay apart

Halves that stay apart are linked cell by cell, through stretches. A *stretch* is a line of floor cells on one half, one after another along the fold. A wall or door between two of its cells ends it. With the rows aligned by the half pair's row shift, stretches on the two halves pair from the largest overlap of rows down, and equal overlaps pair from north to south. Each stretch pairs at most once. Within a pair of stretches, cells in the same row pair with each other. A pair of stretches that share no row pairs its cells one by one in north-then-west order. Passages, one in each direction, link each pair of cells. A stretch or cell left without a partner ends at the fold. The rules that find ownership and areas treat each pair of cells linked across the fold as joined by an open edge, so an area that the fold cuts stays one area.

## Entrances

Each dungeon gets exactly one entrance. These rules always give one:

1. The entrance area is the first of these that exists:

   - the map-reading stage's `entrance_key`
   - the first key in survey order titled exactly "Entrance" or "Entranceway", ignoring case
   - the first area in survey order that a walk from an outer opening reaches across unkeyed floor alone, with no door or other area on the way
   - the area with the most edges against outside floor
   - the first area in survey order.

2. The chosen opening is the one the walk in rule 1 came from, or else the outer opening nearest the area's first cell by walking distance.
3. The entrance cell is the entrance area's cell that touches the middle of the chosen opening, north or west of the middle when the opening's width is even. When the area touches no opening, the entrance cell is the floor cell just inside the chosen opening. With no outer opening at all, it's the area's first cell.

The entrance stands on the level that the entrance cell belongs to. The report flags each choice that doesn't come from the first source of its rule.

## Links

A *link* is a prose connection to another level, or to an area on another panel of the same level, that the trace doesn't already make through floor, doors, or passages. A link stands on every stair, ladder, trapdoor, or chute symbol that touches or adjoins its area and points the way the link goes, or on any such symbol when the map shows no direction for it. A hinged floor counts as a trapdoor, and a slide counts as a chute. A link's realization is a transition: `stairs_up` or `stairs_down` for stairs and ladders by the way the link goes, `trapdoor` or `chute` for the rest.

The link's end at its target stands by the same rule, on a symbol beside the target area that points back or shows no direction. On a symbol several cells long, an end takes the cell farthest from the first cell of its own area: the link's area for the end where the link starts, and the target area for the end at the target. The ends on each level or panel are placed by this rule separately, and the ends of a link on two levels or panels are paired in north-then-west order. An end with no partner lands on the target area's first cell. A link with no symbol at all stands on its area's first cell. A link whose target level doesn't exist is dropped. The report flags each of these. A repair never turns a cell with a link's symbol into rock.

## Passages

A passage stands on a cell and one side of it. The side is a wall in the level's edges, and the cell one step across the side is the passage's far cell. A passage leads to a cell of a level of the same dungeon, which may be the same level when the passage links two panels. Its *partner* leads back: it stands on the first passage's destination cell, on the side opposite the direction the party arrives facing, and lands on the first passage's cell. The rules make passages only in pairs.

## Checks and repairs

After the rules run, the checks walk each dungeon from its entrance, through doors, secret doors, and the dungeon's own transitions and passages, and look for an area that can't be reached, floor cut off from everything, a door with neither floor nor the outside on one side, a bypass, and a dispute. A repair is the cheapest change that fixes a fault, and the truth format records each one as a `Repair` with the edges and cells it changed and its cost. A trace that made repairs also records the document as the rules left it before any repair, so the scorer can score both.

## The scorer

The scorer compares a trace with its answer key, both in the truth format.

- A 5-foot panel in either document is converted first.
- Each panel of the trace is lined up with the key's panel of the same id at the whole-cell offset, within 3 cells each way, that makes the most floor cells agree. Ties go to the offset nearest zero, then to the first in north-then-west order.
- The floor, edges, and doors families compare the panels, so they measure the reading. The areas, keys, joins, reachability, entrance, transitions, passages, and repairs families compare the levels, so they measure the resolution. A level cell is mapped through the panel it lies on: to that panel's own coordinates by the trace's panel origin, across by the panel's offset, and onto the key's level by the key's panel origin.
- A level that fell back in either document scores in the verdicts family only.
- Two areas of a level adjoin when a passable edge or a door joins a cell of one to a cell of the other. The joins family counts joins the trace lacks and joins it adds.
- The edges universe is the key's edges that touch floor or water. An edge is right when the trace reads it as passable or blocked the way the key does. Walls read as open are counted on their own. Edges with a cave cell on either side are tallied apart from the rest.
- Doors are tallied by kind: `d`, `l`, and `s`. A door matches when the trace has the same kind on the same edge.
- A key lands right when the trace's placed cell for it lies in the key's area of that name and the key's placed cell lies in the trace's area of that name.
- Reachability walks each dungeon from its entrance across passable edges, doors of every kind, transitions, and passages. A dungeon is right when the trace reaches the areas the key reaches and no others.
- An entrance, transition, or passage is right when the trace has one on the same cell with the same kind, side, and destination.
- A half pair is right when the trace joined or left apart the halves as the key did, with the same row shift and, when joined, the same column offset.
- A region is right when its verdict, style, scale, north, and legend page match the key's. A level is right when its verdict matches.
- The scorer scores the trace as repaired and, when the trace records one, the trace before repair, and each of those both over every cell and without the cells the key marks as partly floor and the edges that touch them.

## The synthetic maps

The synthetic maps are drawn from levels written in the truth format, in four styles: a TSR blue map scanned at 150 dpi with JPEG noise and uneven pitch, a black and white map, dark rock with white passages, and a gridded cave with walls drawn off the grid. Their answers are exact and no model made them. They're committed under `tests/assets/synthetic-maps/`, and a test regenerates them and fails on any difference: every file byte for byte, except a JPEG image, whose decoded pixels must match the committed image's to within 6 on every channel of every pixel and 0.5 on average. The generator also writes each map's survey, content, and map-reading caches in the stage models.
