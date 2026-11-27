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

The full run reads every map with a first answer key, and every synthetic map, as the stage reads it. It resolves each member's reading into a trace with the reference resolution script (`tools/answer_keys/resolve.py`), scores the trace against its key with `score_trace`, and runs the structural checks of "When part of a trace fails". A *member* is one answer key, or one synthetic map's answer, together with the caches it resolves with.

The full run is made of these modules:

- `full_settings.py` reads the settings file, which is stored with the results. The file sets each kind of Claude request's model and effort, the request cap, the Clef backend and its hosted fallback's spend, the passable thresholds, ε, and the levels that milestone 1 looks at.
- `full_members.py` lists the members: the real ones from the map, pages, and answer-key spec files, and the synthetic ones from `tests/assets/synthetic-maps`.
- `full_store.py` files every Clef answer and Claude outcome under the key of the request that was sent, so the run reuses an answer only for the same request. The run sends a request that isn't in the stores to its source: live, fake, or none.
  - The run writes each Claude outcome to a temporary file and renames it into place, so a crash can't leave a partly written file.
  - A crash while the run appends to the Clef store can leave a torn last line. The next run reports the line and drops it.
  - When the Clef store's last line is whole but lacks its newline, the next run keeps the line and writes the file again, so the next answer starts on a line of its own.
- `full_seed.py` fills the stores with the answer-key pass's and the screening's answers.
- `full_reading.py` reads a map in the plan's order: the whole-page read, the ink, Clef on every edge that touches floor or an unsettled cell, the calibration, the uncertain items, Claude's judgments, keys from marks, and the offset picks with the halves rule. Its docstring states each step's rule.
- `full_checks.py` runs the structural checks on a resolved document with the scorer's walk: unreachable areas, cut-off floor, doors without floor or the outside, bypasses, and disputes.
- `full_score.py` scores a level on its own documents and resolves a member's reading.
- `full_pools.py` splits shared panels by level, pools the families by style, and builds the bar.
- `full_costs.py` places every request on a level row or a named row.
- `full_report_check.py` checks that a report's figures add up. It checks each member's split by style against its whole score, each pool against its members, each panel's parts against the panel, each level row against its parts, the bar's joins over the same levels for every reading, and the costs against the tally. The check fails when a member in the report has no scores.
- `full_run.py` drives the run and writes the report.
- `full_pictures.py` draws milestone 1's levels, each beside the same trace with its faults marked.
- `full_fakes.py` makes answers from the answer key, for tests and offline runs.

The calibration a map is read with leaves out every map cut from an image with the same bytes: the map itself, the other regions of its page, and a synthetic twin drawn from the same image. Claude's answer on an item replaces the item's probabilities, weighted by Claude's accuracy on such items of the style as the screening measured it. At the screening's accuracies, 0.83 to 1 per style and kind, and the passable thresholds, every weighted reading is Claude's own answer.

The uncertain items are of two kinds. The first is each edge between two cells the half rule makes floor whose calibrated P(passable) lies strictly between ε and 1 - ε, unless Clef reads a door there. The second is every cell the ink leaves unsettled. The plan's other kinds of item need the checks-and-repairs loop of osr-forge chunk 7. That loop isn't built yet, so the run sends none of them.

### What the report contains

- **Every scorer family per level,** over every cell and without the partly-floor cells, with cave edges apart from straight ones.
  - A level's floor, edges, and doors are its part of each panel it uses. On a panel that one traced level of the key uses, that part is the whole panel. On a panel that several levels share, such as page 38's, it is the cells the level's grid in the key makes floor or water.
  - An edge counts for the level of its west or north cell, or for the level of its east or south cell when no level owns the first. So an edge on the boundary between two levels counts for the level west or north of it.
  - The cells that belong to none of the levels, such as page 38's ravine, are the panel's *no level* part. The parts of a panel add up to its score.
  - The level's other families come from documents where every other level fell back and only the level's panels are kept. These figures are for display only. They leave out the level's passages to other levels, and the reachability check walks from a stand-in entrance, so they don't add up to the member's figures.
- **What is held out.** The calibration a map is read with was fitted without the maps cut from its image, so the calibration's fit is held out. The passable thresholds and ε aren't held out, because the screening chose them on the same answer keys, by the stage's reading of every key edge at each threshold and the band at each ε (step 5 under "Run it").
- **Every family per member, split by style, and pooled by style,** real maps apart from synthetic ones. A pool adds up its members' splits, so every family of a pool adds up its members' figures. A member's split counts each of the following once, under one style:
  - each panel by the panel's style
  - each area, key, join, transition, and passage by the style of the level it stands on. A passage between levels of two styles counts for the style of the level it leaves from, where the scorer lists it.
  - each region and level, in the verdicts family, by its own style
  - each dungeon's entrance and reachability by the style of the level the key's entrance stands on
  - each pair of halves by its first region's style

  As in the scorer, a member's figures leave out a panel when every level that uses it fell back. A level that the key draws on none of its panels, such as the caves' crypt, counts in the `none` pool.
- **The bar for each style of the answer keys,** with the stage's figures beside the classifier's:
  - Whether each edge is passable or blocked, over every edge of the keys, and the rate of walls read as open.
  - The joins between areas on every traced level of the keys. A reading that fell back on a level lacks every key join of that level, so the bar charges both readings on the same levels, and it lists the levels each reading fell back on. The classifier reads only cells and edges, so its trace resolves them on the key's outlines, marks, and symbols, and its join errors leave out the errors of reading those. The stage's trace resolves its own outlines and marks, so its join errors include theirs. The bar also reports the stage's join errors with its cells and edges resolved on the key's reading, as the classifier's are. The pass rule uses the stage's own trace.
  - Both readings on the *settled edges*: the edges where the answer-key pass's readers disagreed and its settler decided. The bar counts them on the scorer's 10-foot grid within its edge universe, so they are a subset of the edges above. On a 5-foot map, an edge counts as settled when the pass settled any 5-foot segment that the key's conversion lays on it. The classifier and Clef's top answer were among the pass's readers, and a key edge is undisputed only where those readers agreed. So on undisputed edges the classifier and Clef's top answer are right by construction, and the settled edges are the column that compares the readings fairly.
  - The members left out, in two lists. `no_stage_trace` lists the members the stage has no trace of, such as a member with a page that has no whole-page answer. `no_classifier_reading` lists the members with a map that the classifier has no reading of.

  A style passes when the stage reads no more walls as open, gets more edges right, and makes fewer join errors than the classifier. A style with a member in `no_stage_trace` isn't judged: its `passes` is `null`, and the printed report says `not judged` and names the members.
- **Whether each level *needs no repair*:** no unreachable area, no cut-off floor, no door without floor, and a dungeon that can be walked from its entrance. A bypass or a dispute changes no geometry, so the report lists it but doesn't count it. The report shows the answer key's checks beside the trace's, because the keys fail some of them.
- **Whether each level's reading is complete:** the items Claude left without an answer (missing from the run, malformed after the retry, or refused twice) and the edges between floor that became walls for want of a Clef answer, both counted on the level.
- **Every request the run counted,** once each, on one row. A *level row* contains the requests about one level. A *named row* contains the requests that don't belong to a level row, such as the real pairs' offset picks, a whole-page read of a page with several levels, or the requests about a level of an unread member (`<member>: <dungeon>/<level> of an unread member`). An *unread* member is one with a page that has no whole-page answer, so the report has no level rows for it.
  - Each row splits its requests and dollars by origin: sent by this run, answered by an earlier invocation, reused from the screening or the answer-key pass, fake, or missing.
  - The rows add up to the run's totals.
  - A live run checks its Claude attempts against what it added to the Claude ledger, one entry per attempt. It checks its hosted Clef questions against what it added to its entry on the Clef ledger, in requests and dollars. Each Claude attempt is charged its ledger entry, so the two agree to the micro-dollar.
  - When an earlier invocation paid for attempts at a request and didn't finish it, the run counts those attempts on the request under the origin for an earlier invocation, with no answer. They appear in the rows but not in this run's ledger check.
- **Claude's accuracy on the items** per style and kind: the screening's, which weights the answers, and this run's against the keys.
- **How each member resolved.** When the reference script meets a case that the conventions don't answer, the level in that case falls back, the other levels resolve again, and the report lists each case.
- **The inputs** (`inputs`): the sorted member ids, each member's maps, the real pairs, whether the screening's seeds were given and how many were filed, and the SHA-256 hash of the settings file. A live run compares them with the dry run's.

### Run the full run

In these commands, `R` and `P` are as in "Run it", and `S` is the settings file. Add the commercial files after each committed file, as in "Run it".

1. Run the dry run. It sends nothing, and it takes about 13 minutes with every member:

   ```text
   S=$R/full-run/settings.json
   uv run python -m tools.reading.full_run --settings $S --out $R/full-run --maps tools/reading/maps.json \
       --pages tools/reading/pages.json --keys tools/answer_keys/keys.json --halves $P/halves.json \
       --synthetic tests/assets/synthetic-maps --screening $R --classifier $R/classifier \
       --dry-run --ledger ~/.re-pod/map-plan/claude-ledger.json
   ```

   The dry run reads with every stored answer and lets the answer key's whole-page answer stand in where none is stored. It writes `dry-run.json` to `--out`, with the requests and dollars each map still needs (`estimate`) and the run's inputs. `--ledger` only reads the ledger, to count what the cap leaves.

2. Check the report's arithmetic. The check prints each check and exits 1 on a failure:

   ```text
   uv run python -m tools.reading.full_report_check $R/full-run/dry-run.json
   ```

3. Read the estimate, and check that its count of Claude requests fits within what the cap leaves.

4. Set the ledgers and credentials, which every live run needs:
   - `OSRFORGE_CLAUDE_LEDGER` must name the settings' ledger, the one the cap counts. The run refuses to start otherwise.
   - The run reads the API key from the `ANTHROPIC_API_KEY=` line of `~/repos/re-pod/.env`, inside the process.
   - A run that might use hosted Clef needs `OSRFORGE_CLEF_ACCOUNT_ID`, `OSRFORGE_CLEF_LEDGER`, and a wrangler login or `OSRFORGE_CLEF_TOKEN`. It checks for them when it starts and refuses to start if any is missing.
   - Local Clef needs the maintainer's System One server, such as Ollama with `clef-flash`. Never start, stop, or restart it.

5. Run the live run with the same arguments as the dry run, with `--claude live --clef local` in place of `--dry-run`:

   ```text
   uv run python -m tools.reading.full_run --settings $S --out $R/full-run --maps tools/reading/maps.json \
       --pages tools/reading/pages.json --keys tools/answer_keys/keys.json --halves $P/halves.json \
       --synthetic tests/assets/synthetic-maps --screening $R --classifier $R/classifier \
       --claude live --clef local
   ```

   The live run refuses to start unless `--out` holds a dry-run report whose inputs equal its own, and the message states what differs. So leaving out `--synthetic`, a commercial `--maps`, `--pages`, or `--keys` file, `--halves`, or `--screening`, or changing the settings file, stops the run before it sends anything. Run the dry run again with the same arguments, then the live run.

6. Read the printed report. Each reason the run failed is on a `FAILED:` line at the end. If the run stopped, fix what the line reports and run the same command again. A rerun sends only the requests that aren't in the stores.

The options:

- `--dry-run` sends nothing, as step 1 says.
- `--claude live` asks Claude within the settings' cap. It refuses `--only`, `--clef fake`, and `--stand-in`, because each would choose items that a run of every member never asks: `--only` fits the calibration on fewer members, `--clef fake` chooses items on fake Clef answers, and `--stand-in` chooses them on the key's whole-page answers.
- `--claude fake` answers from the answer key, so its scores measure the run's plumbing and the ink, not Claude.
- `--clef local` asks local Clef-flash, and hosted Clef-flash for a question that local fails on by itself: an HTTP error, a 5xx after the provider's three attempts, or an answer it can't read. Each answer is filed under the request actually sent.
- `--clef local-only` leaves out the fallback, so a question local fails on stays unanswered.
- `--clef hosted` asks hosted alone, and `--clef fake` answers from the answer key.
- `--classifier` is the directory of the classifier's readings, for the bar.
- `--pictures` draws milestone 1's levels into `pictures/`.
- `--claude-for <member>` limits Claude's item requests and offset picks to the named members' maps and pairs, and the real pairs, so you can take the members one group at a time within the cap. The run sends every page's whole-page read whichever members you name, because it fits each style's calibration on every map of that style, and every map's item list depends on the calibration.
- `--only <member>` runs the named members alone, for dry and fake runs. The run fits their calibration on their maps alone, so their item requests differ from the ones a run of every member sends.

The settings' hosted spend covers every invocation of the full run together:

- Each invocation can add the spend minus the total of every earlier full run's entries (`reading full run`) on the Clef ledger. The run prints what is left when it starts.
- The hosted provider writes one entry per invocation to the Clef ledger and adds each request's cost and count to it. Hosted questions go one at a time, so each is charged the requests and dollars it added to that entry.
- Once the remaining spend can't cover a question's worst case, that question and every later question for hosted stay unanswered, the run goes on, and the report's `hosted_spent_out` states why. The run reads each such edge as a wall without Clef.
- Each such edge also holds back every item request of a later live run (see the phases below). The item requests wait until local answers those edges on a rerun or you raise the settings' hosted spend.

A run goes in phases, so every request it sends is one that a run of every member would also send:

1. Every page's whole-page read, the ink, and Clef on every map's edges.
2. The calibration, fitted on every map that phase 1 read.
3. Each map's uncertain items and Claude's judgments of them, which `--claude-for` may restrict, then keys from marks. A live run sends no item request while a page lacks a stored whole-page outcome or an edge lacks a Clef answer, because the run would choose the items on a calibration that lacks them. The report's `waiting_for` lists those pages and counts those edges per map, and `items_held` states why the run held the items back. The rule applies with `--clef none` too, unless the Clef store already has an answer for every edge.
4. The offset picks, which `--claude-for` may restrict.

### When something goes wrong

The run stores every attempt that reached the ledger, with what its ledger entry charged, before it does anything else. A request whose attempts ended without a final outcome keeps them in `claude/<kind>/<key>.pending.json`. A *final* outcome is one that sending the request again can't change: an answer, an answer malformed after the one retry, or a second refusal. The next invocation sends a pending request again, counts its earlier attempts, and lists them in the stored outcome as `earlier_attempts`.

A *stop* means that the run sends nothing more but still finishes. It counts every request after the stop as missing, writes the report, the traces, and the ledger checks, and states the reason in the report's `stopped` and on a `FAILED:` line.

- **The account runs out of credit or reaches a usage limit.** The API answers 400 with a message that names the credit balance, billing, or a usage limit. The run stores the attempt as pending and stops, and the `FAILED:` line prints the API's message. Add credit or raise the limit, and run again. While the account stays at fault, each invocation stops at its first request, which costs one cap entry and $0.
- **The API refuses the key or the account (401 or 403), or a failure is transient** (a 5xx, a lost connection, 429s past the provider's five attempts, or a 408 or 409). The run stores the attempts as pending and stops. Run again when the cause is gone.
- **The API refuses a request for a fault in the request** (400, 404, 413, or 422). The run stores the first refusal as pending, with the refusal recorded, and stops, so the fault affects at most one request before you look. The `FAILED:` line names the request's kind, its page, map, or pair, and the API's message. A 404 for the model, for example, is a fault in the settings or the key, so fix that first. Then run again: the run sends the request once more. A second refusal stores the request as final with no answer, the run never sends it again, and it goes on to the next request. The report's `refused` lists each request refused twice. So a request with a real fault costs two cap entries. To send it again later, delete its `claude/<kind>/<key>.json`.
- **A whole-page read ends without an answer** (malformed after the retry, or refused twice). The member is unread: its resolution in the report starts with `unread` and lists the page, it gets no scores, the bar doesn't judge its styles, the run exits 1, and the report check fails.
- **The cap or the ledger's stop refuses an attempt.** The run stores the attempts made before and stops. A request refused before its first attempt keeps no pending file and is counted missing.
- **You press Ctrl-C.** The run stops: it sends nothing more, lets the request in flight finish and stores it, and then finishes with what is stored. That takes about as long as a dry run, after which it writes the report and the ledger checks and exits 1. Press Ctrl-C again to end the process at once. The run then stores the attempt in flight as pending and writes no report. Any other exception during a request also keeps its paid attempt as pending before the run ends.
- **The local Clef server stops answering.** When local refuses or loses the connection, or times out, the server is down or hung, so the run stops instead of sending every question to hosted. When local fails 3 questions in a row in another way, such as a 500 or a 404 for every question because the model isn't loaded, the run stops too, and the message names the last failure. Fix the server and run again. The questions that went to hosted before the stop are stored, so a rerun doesn't ask them again.
- **Hosted Clef fails on a question it charged for.** The run stops, with the charge counted.
- **You leave out an input file or change the settings.** A live run refuses to start (step 5).

The run exits with status 1, after it writes the report, when it stopped, when a member is unread, when a live run held its item requests back, when the cost rows don't add up to the run's totals, or when a ledger check disagrees. It prints each reason on a `FAILED:` line, after both ledger checks and the row sum. Otherwise it exits with status 0.

Only one full run can use an output directory at a time. A run holds an exclusive lock (`fcntl.flock`) on `full-run.lock` in `--out` while it runs, and writes its process ID to the file. Another run refuses to start while the lock is held. The system releases the lock when the process ends, however it ends, so a crash leaves no lock behind. The lock can't stop two runs with different `--out` directories from sending to the same Claude ledger, so don't start two such runs.

### What the dry run found

These figures are from a dry run with every stored answer and the Clef store filled from local Clef-flash:

- **Requests.** Claude still needs 323 requests, and the cap leaves 352. They would cost about $28.19 at the screening's mean dollars a request. With one retry each the requests would be 646, but none of the screening's 127 answered requests needed one. Clef still needs 3 mark questions.
  - The real maps need 123 item requests, since their whole-page reads and the offset picks are stored.
  - The synthetic maps need 29 whole-page reads and 171 item requests, 109 of them on the nine black-and-white maps.
  - The estimate counts each request once, so twin maps' shared item requests count once. It gives an upper bound beside the count only when an edge lacks a Clef answer, because only such an edge could add items. Here every edge has a Clef answer, so the bound equals the count. The bound gives each map's possible items their own requests, so it may count a request that twins with partly different item lists share twice, which errs toward too many.
- **Walls read as open.** Before Claude's item judgments, the stage reads fewer walls as open than the classifier in every style of the keys: 19 against 34 on TSR blue, 16 against 25 on black and white, 52 against 56 on cave, and 3 against 4 on dark rock.
- **Edges right.** The stage gets fewer edges right than the classifier in every style but dark rock, where both get 556 of 628. So no style passes the bar yet.
- **Joins.** Over the same traced levels for both readings, the stage makes these join errors against the classifier's:
  - black and white: 29 against 31, of 43 key joins
  - cave: 56 against 35, of 77
  - dark rock: 7 against 8, of 13
  - TSR blue: 60 against 59, of 71

  The stage's trace fell back on two levels, the halfling home (black and white) and orc lair A (cave), and is charged every key join of each. The classifier's trace fell back on none. With its cells and edges resolved on the key's outlines, marks, and symbols, as the classifier's are, the stage makes 23, 54, 8, and 57 errors.
- **Settled edges.** On the edges the answer-key pass's settler decided, on the 10-foot grid, the stage gets 405 of 637 right against the classifier's 535 on black and white, 1,788 of 2,065 against 1,881 on cave, 145 of 206 against 144 on dark rock, and 344 of 461 against 351 on TSR blue.
- **Pools.** The real pools count each region and level once. The cave pool has 9 of its 10 level verdicts right, and black and white 2 of 3. The caves' crypt, which isn't drawn on any panel, is the `none` pool's one level. Chaotic Caves' 3 key passages are all in the cave pool, none of the trace's passages match them, and the trace adds 48. Reachability is right for 1 of the 12 dungeons the real pools walk.
- **Page 38.** Its ten lairs own 1,534 of its 2,082 floor cells. The other 548, the ravine between the lairs, belong to no level.
- **Unsettled cells.** The ink leaves 15% to 37% of a real map's cells unsettled. Ink inside a cell is the largest reason on five of the seven real maps. Partly floor leads on the dark-rock map, and the grid on fewer than two sides on one TSR blue map. The ink leaves 20% to 39% of a synthetic TSR blue map's cells unsettled, mostly because their faint grid fails the two-sides test, and 91% to 97% of a synthetic black-and-white map's, where hatched rock reads as partly floor on all but one of the nine. The second command of step 4 under "Run it" prints these figures.
- **Levels on two regions.** The whole-page request answers each level on one region, so a level drawn on two regions of one page (the synthetic shrine and watchtower) keeps an outline on one of them only.
- **Twin halves.** Each synthetic map whose halves stay apart shares its halves' images with its joined twin, so one offset pick can't be right for both.
- **The keys' own checks.** The answer keys fail some checks themselves: 11 of the 16 real levels have a fault a repair would change, such as floor beyond the manor's moat, which the walk never crosses. Every synthetic answer passes.

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
- The scores' meaning: the level rows on a shared panel adding up to the panel, with a boundary edge counted for its west or north level, page 38's lairs and the ravine no level owns, each member's split by style adding up to its whole score family by family, the bar's joins charged on a level a reading fell back on, a member without classifier readings left out of the bar, the stage's joins on the key's reading, the settled edges of a 5-foot map on the 10-foot grid, the milestone pictures veiling what a row doesn't score, and the report check on a run on fake answers.
- The live paths, on stand-ins for the services: a run refused when its Claude ledger isn't the settings', the cap of 480 stopping the next request, a malformed attempt charged what the ledger settled with each attempt counted, a local server refused when it names another model for Clef-flash, and questions local Clef fails on going to hosted Clef within the spend, each filed under the request sent at what it added to the run's Clef ledger entry. The ledger check agrees with the tally on both ledgers.
- The bar beside the classifier per style, and the settler's edges from the answer-key pass.
- What the full run pays for, on stand-in providers and temporary ledgers: every paid attempt kept through a failure, the cap, a refusal, Ctrl-C, or another exception; a first refusal that stops the run and a second that stores the request with no answer; a 400 that names the account kept pending; the real Claude provider's errors as `ask` classifies them; the phases that make a partial run's requests a full run's; a live run refused without a dry run of the same inputs; an unread member that fails the run, the report check, and the bar's verdict; the hosted spend held over invocations; a local server that fails three questions in a row; hosted charges counted from the ledger; the estimate's bound; a Clef store line without its newline; and the lock held by `flock`.

No test calls a service or reads a ledger outside its temporary directory.
