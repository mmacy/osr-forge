# The reading measurement

The tracing stage reads each printed map with three readers: Clef, a deterministic ink measurement, and Claude. See `docs/plans/map-tracing.md` in re-pod, "Reading the map" and step 8 of "Measurements before the contracts". This tool measures how well that reading scores against the answer keys before the stage exists, with the scorer's own code. It is the reference for osr-forge chunk 8.

The plan's step 8 runs in two parts. The screening, which is done, chose the stage's settings:

- the bar for walls read as open
- how Clef is asked, and the calibration with its thresholds
- how the ink settles cells, and how keys are found
- Claude's model and the effort for each kind of request
- the Clef backend

The full run reads, resolves, and scores every map with those settings (see "The full run"). It waits for the answer keys' approval.

Commercial maps' specs, answers, and results stay outside the repository, under `~/.re-pod/map-plan/osr-suite/reading/`. The files here cover the committed Chaotic Caves maps and synthetic maps.

## The parts

- `traces.py` scores a reading of one panel with `score_trace`, on a one-panel document holding the key's panel and its region. Where a script needs the edges the scorer counts, it calls the scorer's own `edge_universe`.
- `bar.py` gives the bar for walls read as open: the classifier's rate on the answer keys, per style. The rate is walls read as open over the scorer's edge universe, pooled over a style's maps.
- `ink.py` and `marks.py` are the stage's ink measurements, in numpy alone. They share no code with the answer-key classifier.
  - `ink.py` gives each cell's floor fraction and the line across each edge (`width`, `heavy`, `line`). It also gives the cells the ink leaves unsettled.
  - `marks.py` finds the label-sized marks and builds Clef's key and letter questions.
- `clefask.py` asks the stage's Clef questions.
  - An edge crop is 2 by 2 cells at 128 pixels per cell. A cell crop is 3 by 3 cells at 96 pixels per cell. Each crop is resampled through the registration and carries a red box on the item.
  - Answers go to resumable JSON-lines stores on the key's grid.
  - It screens four edge option sets:
    - `keyset`: the answer-key pass's set for each style, the one the stage uses.
    - `seven`: the plan's seven options in every style.
    - `sevenk`: the seven options in the answer-key set's words.
    - `keysetp`: the answer-key set plus a printed-open-line option.
- `answers.py`, `edges.py`, `dataset.py`, and `calibrate.py` hold the data and the calibration.
  - `answers.py` loads answers onto the key's grid.
  - `edges.py` gives the measured edges (both cells floor or water) and the threshold decision.
  - `dataset.py` and `calibrate.py` fit the calibration: a ridge logistic model per style. Each map is scored with a model fitted without it.
- `screen_clef.py` compares option sets and backends per style:
  - passable or blocked on the measured edges, raw and calibrated at each threshold
  - the share of answers, ranked by confidence, before the first and the fifth wrong one
  - each letter's recall
- `screen_letters.py` compares option sets letter by letter, with no calibration, on the windows that every set answered whole: each letter's recall, the raw passable errors, and what a set answers instead when it is wrong.
- `screen_cells.py` scores the ink's cells on whole maps (floor or not by the half rule, the cells left unsettled, and the settled cells that are wrong) and Clef's cell question on the cells the ink leaves unsettled.
- `stage_reading.py` and `screen_bar.py` give the stage's reading before Claude.
  - A cell is the ink's, or Clef's where the ink leaves it unsettled.
  - An edge between floor cells is the door Clef reads when a door kind is Clef's top answer, and otherwise the calibrated decision.
  - An edge against rock is a wall, or the door Clef reads there.
  - The reading is scored against the classifier's reading of the same edges. The script also counts the band Claude would judge (edges with calibrated P(passable) between ε and 1 - ε), per map, with the errors inside it.
- `screen_synthetic.py` applies the real keys' calibrations to synthetic maps with exact answers, with and without the other synthetic maps in the fit. It also gives the calibration with the ink without doors first, and counts the doors each calibration reads as open.
- `screen_marks.py` finds the marks, asks Clef which key each is, and scores both against the key's marks. It also scores the keys with each key keeping only its most probable mark.
- `export_stored.py` puts the answer-key pass's stored Clef answers onto the keys' grids for `screen_bar`.
- `hosted_exported.py` rebuilds the answer-key pass's exported edge requests and checks each against its exported key, so its crop, state, question, and options are the exported request's byte for byte. Hosted Clef gets the request as exported. Hosted Clef-flash gets the same request with only the model changed. Either way the backends pair with the stored local answers on the same crop and text.
- `screen_backends.py` compares the backends on those identical requests, with no calibration: how often their top answers agree, each letter's recall, and the passable errors at one half and at the best threshold.
- `wholemap.py`, `items.py`, and `offsets.py` are the three kinds of Claude request. Their drivers are `screen_wholemap.py`, `screen_items.py`, and `screen_offsets.py`.
  - Every request goes through the stage's provider, `ClaudeProvider`, with one retry of a malformed answer and a cap on the screening's requests (`claude_run.py`).
  - `pictures.py` draws a region with its grid ticked.
  - `screen_summary.py` pools the stored outcomes by model and effort. It also scores the decision the items were chosen by on the same items, and counts the screening's entries on the Claude ledger.
- `full_run.py` and the `full_*.py` modules are the full run (see "The full run").

## The files

Each file here except `windows-variants.json` has a counterpart for commercial maps, in the same format, under `~/.re-pod/map-plan/osr-suite/reading/specs/`.

- `maps.json` lists each map region with its key, its image, the answer-key work directory, the frame from the working grid to the key's grid, and its classifier spec.
- `windows.json` lists the screening windows: per map, the 12 by 12 windows with the most universe edges, chosen to hold every rare letter the map has. `windows-hosted.json` lists the window the hosted run read. `windows-variants.json` lists the two black-and-white windows that the option sets `sevenk` and `keysetp` were asked on.
- `pages.json` lists the map pages and the regions on each.
- `halves.json` lists the synthetic pairs of halves, with the letter of their true join.
- `synthetic-maps.json` and `synthetic-windows.json` cover seven synthetic regions whole.

## Run it

Run from the osr-forge checkout. Each driver keeps what it has stored and asks only what's missing. Each needs the following:

- The Claude drivers need `OSRFORGE_CLAUDE_LEDGER`. They read the API key from the `ANTHROPIC_API_KEY=` line of `~/repos/re-pod/.env`, inside the process.
- `--cap` counts every entry on the Claude ledger whose label starts with `reading-`, the screening's 128 among them, and refuses a request once the count reaches the cap. The plan's step 8 had a budget of 350 Claude requests, and the maintainer raised it to 480. The full run takes its cap of 480 from its settings, which leaves it 352.
- The local Clef runs need a System One server, such as the maintainer's Ollama with `clef-flash`. Never start, stop, or restart it.
- The hosted Clef runs need `OSRFORGE_CLEF_ACCOUNT_ID`, `OSRFORGE_CLEF_LEDGER`, and a wrangler login. They stop at their first failure and are held to `--spend` dollars on the Clef ledger.

Add the commercial maps' files after each committed file, as in `tools/reading/maps.json $P/<module>-maps.json`. In the commands, `R` is the results directory and `P` the commercial specs:

```text
R=~/.re-pod/map-plan/osr-suite/reading
P=$R/specs
```

1. The classifier's readings, and the bar:

   ```text
   uv run python -m tools.answer_keys.run tools/answer_keys/maps.json --out $R/classifier
   uv run python -m tools.reading.bar $R/classifier tools/reading/maps.json --out $R/bar.json
   ```

2. Local Clef-flash on the screening windows, one option set at a time, and on the cells the ink leaves unsettled there. The answer-key set's answers are the answer-key pass's own, which the later steps name `keyset-stored`, so that set isn't asked again:

   ```text
   uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows.json \
       --set seven --kind edge --backend local --workers 2 --out $R/clef
   uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows-variants.json \
       --set sevenk --kind edge --backend local --workers 2 --out $R/clef
   uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows-variants.json \
       --only jn1-p046 --set keysetp --kind edge --backend local --workers 2 --out $R/clef
   uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows.json \
       --set seven --kind cell --backend local --out $R/clef
   ```

3. The option sets compared per style. `screen_clef` compares them calibrated, on the edges every set answered, and on every measured edge of the keys when no windows are given. `screen_letters` compares them letter by letter, on the windows every set answered whole:

   ```text
   uv run python -m tools.reading.screen_clef tools/reading/maps.json --windows tools/reading/windows.json \
       --source keyset=keyset-stored --source seven=$R/clef/local/seven --paired --json $R/clef/sets.json
   uv run python -m tools.reading.screen_clef tools/reading/maps.json --source keyset=keyset-stored \
       --json $R/clef/keyset-whole.json
   uv run python -m tools.reading.screen_letters tools/reading/maps.json --windows tools/reading/windows.json \
       --source keyset=keyset-stored --source seven=$R/clef/local/seven --json $R/clef/letters-seven.json
   uv run python -m tools.reading.screen_letters tools/reading/maps.json --windows tools/reading/windows.json \
       --source keyset=keyset-stored --source seven=$R/clef/local/seven --source sevenk=$R/clef/local/sevenk \
       --json $R/clef/letters-sevenk.json
   uv run python -m tools.reading.screen_letters tools/reading/maps.json --windows tools/reading/windows.json \
       --source keyset=keyset-stored --source keysetp=$R/clef/local/keysetp --json $R/clef/letters-keysetp.json
   ```

4. The ink's cells on whole maps, and the cell question. The second command gives each map's unsettled cells by reason, the synthetic maps' included:

   ```text
   uv run python -m tools.reading.screen_cells tools/reading/maps.json --answers $R/clef/local/seven \
       --json $R/clef/cells.json
   uv run python -m tools.reading.screen_cells tools/reading/maps.json --synthetic tests/assets/synthetic-maps \
       --json $R/full-run/cells-reasons.json
   ```

5. The stage's reading of every key edge against the classifier's, with the band per map, calibrated with the ink and without it:

   ```text
   uv run python -m tools.reading.export_stored tools/reading/maps.json --out $R/clef/stored-keyset
   uv run python -m tools.reading.screen_bar tools/reading/maps.json --windows $R/clef/stored-keyset/whole-maps.json \
       --set keyset=$R/clef/stored-keyset --classifier $R/classifier --calibration clef+ink \
       --json $R/clef/bar-whole-keyset-clef+ink.json
   uv run python -m tools.reading.screen_bar tools/reading/maps.json --windows $R/clef/stored-keyset/whole-maps.json \
       --set keyset=$R/clef/stored-keyset --classifier $R/classifier --calibration clef \
       --json $R/clef/bar-whole-keyset-clef.json
   ```

6. The calibrations on synthetic maps with exact answers, with and without doors first:

   ```text
   uv run python -m tools.reading.clefask tools/reading/synthetic-maps.json \
       --windows tools/reading/synthetic-windows.json --set keyset --kind edge --backend local --workers 2 --out $R/clef
   uv run python -m tools.reading.screen_synthetic tools/reading/maps.json \
       --synthetic tools/reading/synthetic-maps.json --answers $R/clef/local/keyset --json $R/clef/synthetic.json
   ```

7. The marks, with Clef's key and letter questions. Without `--ask`, the driver scores the stored answers and asks nothing:

   ```text
   uv run python -m tools.reading.screen_marks tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/clef --ask
   ```

8. The Claude grid, over models and efforts, and its summary. `--ledger` only reads the ledger:

   ```text
   uv run python -m tools.reading.screen_wholemap tools/reading/pages.json --out $R/claude \
       --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
   uv run python -m tools.reading.screen_items tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/claude --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
   uv run python -m tools.reading.screen_items tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/claude --models claude-opus-5-5 --efforts medium high --take 30 --per-request 30 --tag k30 --cap 160
   uv run python -m tools.reading.screen_items tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/claude --models claude-sonnet-5-5 --efforts low --take 30 --per-request 30 --tag k30 --cap 160
   uv run python -m tools.reading.screen_offsets tools/reading/halves.json --out $R/claude \
       --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
   uv run python -m tools.reading.screen_offsets $P/halves.json --out $R/claude \
       --models claude-sonnet-5-5 --efforts low medium --tag .t2 --cap 160
   uv run python -m tools.reading.screen_summary $R/claude --ledger ~/.re-pod/map-plan/claude-ledger.json \
       --json $R/claude/summary.json
   ```

9. The backends on identical requests:

   ```text
   uv run python -m tools.reading.hosted_exported tools/reading/maps.json --windows tools/reading/windows-hosted.json \
       --out $R/clef --model clef --workers 4 --max-requests 470 --spend 0.15
   uv run python -m tools.reading.hosted_exported tools/reading/maps.json --windows tools/reading/windows-hosted.json \
       --out $R/clef --model clef-flash --workers 4 --max-requests 470 --spend 0.15
   uv run python -m tools.reading.screen_backends tools/reading/maps.json --hosted $R/clef --json $R/clef/backends.json
   uv run python -m tools.reading.screen_clef tools/reading/maps.json --source local=keyset-stored \
       --source hosted=keyset-hosted-stored --paired
   ```

## The full run

The full run reads every map with a first answer key and every synthetic map as the stage reads it, resolves each member's reading into a trace with the reference resolution script (`tools/answer_keys/resolve.py`), scores the trace against its key with `score_trace`, and runs the structural checks of "When part of a trace fails". A *member* is one answer key, or one synthetic map's answer, with the caches it resolves with.

- `full_settings.py` reads the settings file, which stays with the results: each kind of Claude request's model and effort, the request cap, the Clef backend and its hosted fallback's spend, the passable thresholds, ε, and the levels milestone 1 looks at.
- `full_members.py` lists the members: the real ones from the map, pages, and answer-key spec files, and the synthetic ones from `tests/assets/synthetic-maps`.
- `full_store.py` files every Clef answer and Claude outcome under the key of the request that was sent, so an answer is reused only for the same request. `full_seed.py` fills the stores with the answer-key pass's and the screening's answers. A request the stores lack goes to the run's source: live, fake, or none.
- `full_reading.py` reads a map in the plan's order: the whole-page read, the ink, Clef on every edge that touches floor or an unsettled cell, the calibration, the uncertain items, Claude's judgments, keys from marks, and the offset picks with the halves rule. Its docstring gives each step's rule.
- `full_checks.py` runs the structural checks on a resolved document with the scorer's walk: unreachable areas, cut-off floor, doors without floor or the outside, bypasses, and disputes.
- `full_score.py` scores a level on its own documents and resolves a member's reading, `full_pools.py` pools the families by style and builds the bar, and `full_costs.py` places every request on a level or a named row.
- `full_run.py` drives the run and writes the report. `full_pictures.py` draws milestone 1's levels, each beside the same trace with its faults marked. `full_fakes.py` makes answers from the answer key, for tests and offline runs.

The calibration a map is read with leaves out every map cut from an image with the same bytes: the map itself, the other regions of its page, and a synthetic twin drawn from the same image. Claude's answer on an item replaces the item's probabilities, weighted by Claude's accuracy on such items of the style as the screening measured it. At the screening's accuracies, 0.83 to 1 per style and kind, and the passable thresholds, every weighted reading is Claude's own answer.

The uncertain items are the edges between two cells the half rule makes floor whose calibrated P(passable) lies strictly between ε and 1 - ε, unless Clef reads a door there, and every cell the ink leaves unsettled. The plan's other kinds of item need the checks-and-repairs loop of osr-forge chunk 7, which isn't built, so the run sends none of them.

The report gives the following:

- Every scorer family per level, over every cell and without the partly-floor cells, with cave edges apart from straight ones. A level is scored on documents where every other level fell back and only its own panels are kept. A panel the key shares between levels, like page 38's, is cut to the level's outline in the key. Every score is held out.
- Every family per member, and pooled by style, real maps apart from synthetic ones. A pool counts each drawn cell and edge once, scoring each panel once, and each level, pair, and request once. As in the scorer, it leaves out a panel used only by levels that fell back, so a pool's figures add up its members'.
- The bar for each style of the answer keys: the stage beside the classifier on whether each edge is passable or blocked and on the joins between areas, over every edge of the keys, the rate of walls read as open against the classifier's, and both readings on the edges where the answer-key pass's readers disagreed and its settler decided. A style passes when the stage reads no more walls as open, gets more edges right, and makes fewer join errors than the classifier.
- Whether each level *needs no repair*: no unreachable area, no cut-off floor, no door without floor, and a dungeon that can be walked from its entrance. A bypass or a dispute changes no geometry, so it's reported and doesn't count. The answer key's own checks are given beside the trace's, because the keys fail some of them.
- Whether each level's reading is complete: the items Claude left without an answer (missing from the run, or malformed after the retry) and the edges between floor that became walls for want of a Clef answer, both counted on the level.
- Every request the run counted, once each, on exactly one level or on a row the report names for what no level owns, such as the real pairs' offset picks or a whole-page read of a page with several levels. Each row splits its requests and dollars by origin: sent by this run, answered by an earlier invocation, reused from the screening or the answer-key pass, fake, or missing. The rows add up to the run's totals, and a live run's totals are checked against the entries it made on the Claude and Clef ledgers.
- Claude's accuracy on the items per style and kind: the screening's, which weights the answers, and this run's against the keys.
- How each member resolved. When the reference script meets a case the conventions give no answer for, the level the case names falls back and the rest resolve again, and the report names each case.

Run it from the osr-forge checkout, with the commercial files added as in "Run it" and `S` the settings file:

```text
S=$R/full-run/settings.json
uv run python -m tools.reading.full_run --settings $S --out $R/full-run --maps tools/reading/maps.json \
    --pages tools/reading/pages.json --keys tools/answer_keys/keys.json --halves $P/halves.json \
    --synthetic tests/assets/synthetic-maps --screening $R --classifier $R/classifier \
    --dry-run --ledger ~/.re-pod/map-plan/claude-ledger.json
```

- `--dry-run` sends nothing. It reads with every stored answer, lets the answer key's whole-page answer stand in where none is stored, and estimates the requests and dollars each map still needs. `--ledger` only reads the ledger, to count what the cap leaves.
- `--clef local` asks local Clef-flash, and hosted Clef-flash, within the settings' spend, for a question local fails on. `--clef local-only` leaves out the fallback, `--clef hosted` asks hosted alone, and `--clef fake` answers from the answer key. Each answer is filed under the request actually sent. Hosted questions go one at a time, so each is charged the Clef ledger entry the provider settled for it.
- `--claude live` asks Claude within the settings' cap, and refuses to start unless `OSRFORGE_CLAUDE_LEDGER` names the settings' ledger, the one the cap counts. `--claude fake` answers from the answer key, so its scores measure the run's plumbing and the ink, not Claude.
- `--classifier` names the classifier's readings, for the bar.
- `--pictures` draws milestone 1's levels into `pictures/`.
- `--claude-for <member>` keeps Claude to the named members, so a run within the cap can take the members in an order. Run every member together all the same: each style's calibration is fitted on every member's maps of that style, so a run on fewer members reads with another calibration.

What the dry run found, with every stored answer and the Clef store filled from local Clef-flash:

- Claude still needs 323 to 330 requests, and the cap leaves 352: about $28.19 to $28.79 at the screening's mean dollars a request. With one retry each they would be 660, but none of the screening's 127 answered requests needed one. The real maps need 123 item requests, since their whole-page reads and the offset picks are stored. The synthetic maps need 29 whole-page reads and 171 to 178 item requests, 109 of them on the nine black-and-white maps.
- Before Claude's item judgments, the stage reads fewer walls as open than the classifier in every style of the keys: 19 against 34 on TSR blue, 16 against 25 on black and white, 52 against 56 on cave, and 3 against 4 on dark rock. It gets fewer edges right than the classifier in every style but dark rock, where both get 556 of 628, so no style passes the bar yet.
- The ink leaves 15% to 37% of a real map's cells unsettled. Ink inside a cell is the largest reason on five of the seven real maps: partly floor leads on the dark-rock map, and the grid on fewer than two sides on one TSR blue map. It leaves 20% to 39% of a synthetic TSR blue map's cells unsettled, mostly because their faint grid fails the two-sides test, and 91% to 97% of a synthetic black-and-white map's, where hatched rock reads as partly floor on all but one of the nine. The second command of step 4 gives these figures.
- The whole-page request answers each level on one region, so a level drawn on two regions of one page (the synthetic shrine and watchtower) keeps an outline on one of them only.
- Each synthetic map whose halves stay apart shares its halves' images with its joined twin, so one offset pick can't be right for both.
- The answer keys fail some checks themselves: 11 of the 16 real levels have a fault a repair would change, such as floor beyond the manor's moat, which the walk never crosses. Every synthetic answer passes.

## The settings

The maintainer decided the following for the stage:

- **Claude model and effort,** per kind of request, each the setting that read best in the screening: Opus 5.5 (`claude-opus-5-5`) at high effort for the whole-page read, Opus 5.5 at medium effort for uncertain items, and Sonnet 5.5 (`claude-sonnet-5-5`) at low effort for the offset pick.
- **Clef backend:** local Clef-flash, with hosted Clef-flash as the cloud fallback.

The screening chose the rest:

- **The edge question:** each style's answer-key option set.
  - Black and white: open, wall, door, window.
  - TSR blue: open, wall, door, secret door.
  - Cave: open, wall, door, locked door, secret door.
  - Dark rock: open, wall, door.
- **Doors first:** a door kind on top of Clef's answer decides the edge before the passable threshold.
- **The calibration:** per style, a logistic model on Clef's passable and door log-odds and the ink's `width`, `heavy`, and `line`. It is fitted on the style's answer keys and synthetic maps.
- **Passable thresholds:** TSR blue 0.7, black and white 0.9, cave 0.9, dark rock 0.8.
- **The band:** ε is 0.05, so the acceptance threshold is 0.95. Requests carry 30 items each.
- **Settling cells by the ink:**
  - The half rule decides floor. The paper threshold is 0.35 on TSR blue and 0.06 elsewhere. Fill is opened by a square a quarter of a cell wide, after a closing of 0.06 cells on black and white.
  - A cell is unsettled when it is between a quarter and three quarters floor, has ink inside, has the printed grid on fewer than two sides, or has a fill tone more than 0.15 from the rock's.
  - Unsettled cells go to Claude. Clef's cell question serves symbols only.
- **Keys from marks:**
  - The key question goes in chunks of 25 keys with `none`, because a System One choice question takes at most 26 options.
  - A letter question is asked only when every chunk answers `none`.
  - Each key keeps its most probable mark.

## What the screening found

The figures pool the committed Chaotic Caves maps and the maintainer's private corpus, as counts by style. Each finding names the step under "Run it" that rebuilds it. The full log, with every command and the private maps' figures, is `~/.re-pod/map-plan/osr-suite/reading/progress.md`.

The bar for walls read as open is the classifier's rate on the keys over the scorer's edge universe (step 1):

| Style | Walls read as open | Edges | Rate |
| --- | --- | --- | --- |
| TSR blue | 34 | 3,380 | 1.01% |
| Black and white | 25 | 3,307 | 0.76% |
| Dark rock | 4 | 628 | 0.64% |
| Cave | 56 | 5,339 | 1.05% |

Claude (step 8):

- **Requests:** the Claude ledger holds 128 screening requests. 127 were answered, and the 127 stored outcomes hold no malformed first answer, so none needed the retry. The 128th was the first whole-page request: the API refused its schema with HTTP 400 before any model ran, and its entry is charged $0, with no outcome stored.
- **Whole-page reads:**
  - Every setting reads the scale and north of all 7 regions right.
  - Every setting except Opus at medium effort reads all 7 styles right.
  - Opus at high effort finds 66 of the keys' 104 symbols, including 40 of 42 stairs and 13 of 16 cave mouths. Sonnet at high effort finds 39.
- **Uncertain items,** 30 to a request on 204 items:
  - Opus at medium effort gets 198 right, Opus at high 193, Sonnet at low 172.
  - The decision the items were chosen by gets 155 of the same items right: the calibrated Clef-and-ink decision on edges, and the ink's half rule on cells.
  - On the first ten items of each map, thirty items a request and ten give the same counts: 67, 65, and 59 of 70.
  - Sonnet doesn't think on item requests at any effort: it writes about 143 output tokens a ten-item request.
- **Offset picks** on two real pairs of halves: Sonnet at low and medium effort picks right 4 of 4 times over two trials. Sonnet at high effort, and Opus at every effort, pick at least one wrong offset, so under the plan's rule the halves stay apart.

The ink settles a cell (floor or not, at half floor) with at most 10 errors in each style's cells that aren't partly floor. It leaves between 8% (dark rock) and 27% (black and white) of them unsettled (step 4).

Clef, on local Clef-flash unless a backend is named:

- **The plan's seven options read doors and windows as open** (step 3).
  - On two black-and-white windows they name 2 of 20 doors and 0 of 22 windows, where the answer-key sets name 16 and 13.
  - On two TSR blue windows they read 141 of 150 walls as open.
  - Any option added to a style's set costs it its windows.
- **Doors and the ink.** A door's box breaks the drawn line, so the ink alone makes a door look open. Without doors first, a calibration with the ink reads all 19 of the synthetic black-and-white doors as open at the style's threshold, 0.9 (step 6).
- **The calibration's expected calibration error,** held out by map on the keys' measured edges, is 0.009 to 0.021 (step 3). Fitted on the real keys alone, it reads 9 to 13 of a synthetic dark-rock map's 15 interior walls as open. With the other synthetic maps of the style in the fit, it reads none (step 6). A style with few blocked edges in its real keys needs the synthetic maps in its fit.
- **Before Claude, on every key edge,** the stage's reading with the ink reads no more walls as open than the classifier (step 5):
  - TSR blue: 16 against 34, at 0.7.
  - Black and white: 23 against 25, at 0.9.
  - Cave: 23 against 56, at 0.9.
  - Dark rock: 3 against 4, at every threshold.
- **Clef alone** misses black and white's bar at every threshold, and needs a threshold of 0.9 on TSR blue and cave (step 5).
- **The cell question,** on the cells the ink leaves unsettled (step 4):
  - It reads floor or not about as well as the ink's half rule on TSR blue (153 against 151 of 241 cells), and worse in the other styles.
  - It reads a plain grey moat as rock: 0 of 33 water cells.
  - It names the stairs and ladders of TSR blue and black and white: 16 of 16 symbol cells.
- **Marks** (step 7):
  - The detector finds a mark within a cell of all 209 key marks.
  - Clef reads 175 of the 209 right, and 5 of 30 single letters.
  - Clef gives a key to 331 of the 521 asked marks that aren't labels. Keeping each key's most probable mark leaves 164 keys right and 35 false keys among 571 other marks.
- **Backends,** on identical requests (step 9):
  - Hosted Clef-flash agrees with local Clef-flash on 96% to 98% of top answers.
  - At its best threshold, hosted Clef reads more open edges as blocked than local Clef-flash on TSR blue, cave, and black and white, and fewer on dark rock's few blocked edges. On cave it names both secret doors, where local Clef-flash names neither.
  - The free Workers AI plan allows about 1,250 requests a day, by the maintainer's account. That is fewer than a large level's 2,000 or so edges.

## Tests

`tests/` runs offline, on the committed Chaotic Caves key and images, the synthetic maps, and made-up answers. It covers the following:

- The scoring: each key scores perfect against itself, and one changed edge counts as exactly one error.
- The crops' sizes and red boxes, and the store's resume.
- Answers moved onto the key's grid by a frame, and exported requests rebuilt with their keys.
- The calibration, the ranked share, and the ink on the manor's upper floor.
- The stage's cells and edges, doors first.
- The whole-page check and score on an answer made from the key, and the item check.
- The retry of a malformed answer with its faults, the request cap, and the summary of stored outcomes.
- The join pictures, and the marks found on the upper floor.
- The findings' tallies: the letters by option set, the ink's cells and the cell question, the backends' agreement and best threshold, a mark's reading and one mark per key, the summary's first ten items, symbols by kind, and the decision the items were chosen by, and the ledger count.
- The full run: the settings, a request counted once with its answer's origin, the key's whole-page answer, a level scored alone on its part of a shared panel, the checks finding an area a removed door cuts off, wrong offset picks, an open case that falls back, and a fault picture.
- A run on fake answers that joins the fold, scores every family, and places every request on one row, the rows adding up to the run's totals. A calibration that leaves out a twin drawn from the same image, a pool that counts a panel two levels share once, and a pool that leaves out the panels of a level that fell back.
- The live paths, on stand-ins for the services: a run refused when its Claude ledger isn't the settings', the cap of 480 stopping the next request, a malformed attempt charged what the ledger settled with each attempt counted, a local server refused when it names another model for Clef-flash, and a question local Clef fails on going to hosted Clef within the spend, filed under the request sent at what hosted charged.
- The bar beside the classifier per style, and the settler's edges from the answer-key pass.

No test calls a service or reads a ledger outside its temporary directory.
