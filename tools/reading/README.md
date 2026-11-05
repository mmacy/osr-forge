# The reading measurement

The tracing stage reads each printed map with three readers: Clef, a deterministic ink measurement, and Claude. See `docs/plans/map-tracing.md` in re-pod, "Reading the map" and step 8 of "Measurements before the contracts". This tool measures how well that reading scores against the answer keys before the stage exists, with the scorer's own code. It is the reference for osr-forge chunk 8.

Step 8 runs in two parts. The screening, which is done, chose the stage's settings:

- the bar for walls read as open;
- how Clef is asked, and the calibration with its thresholds;
- how the ink settles cells, and how keys are found;
- Claude's model and the effort for each kind of request;
- the Clef backend.

The full run, still to come, scores every map with those settings.

Commercial maps' specs, answers, and results stay outside the repository, under `~/.re-pod/map-plan/osr-suite/reading/`. The files here cover the committed Chaotic Caves maps and synthetic maps.

## The parts

- `traces.py` scores a reading of one panel with `score_trace`, on a one-panel document holding the key's panel and its region.
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
  - passable or blocked on the measured edges, raw and calibrated at each threshold;
  - the share of answers, ranked by confidence, before the first and the fifth wrong one;
  - each letter's recall.
- `stage_reading.py` and `screen_bar.py` give the stage's reading before Claude.
  - A cell is the ink's, or Clef's where the ink leaves it unsettled.
  - An edge between floor cells is the door Clef reads when a door kind is Clef's top answer, and otherwise the calibrated decision.
  - An edge against rock is a wall, or the door Clef reads there.
  - The reading is scored against the classifier's reading of the same edges. The script also counts the band Claude would judge (edges with calibrated P(passable) between ε and 1 - ε), per map, with the errors inside it.
- `screen_synthetic.py` applies the real keys' calibrations to synthetic maps with exact answers, with and without the other synthetic maps in the fit.
- `screen_marks.py` finds the marks, asks Clef which key each is, and scores both against the key's marks.
- `export_stored.py` puts the answer-key pass's stored Clef answers onto the keys' grids for `screen_bar`.
- `hosted_exported.py` sends the answer-key pass's exported requests, byte for byte, to hosted Clef or hosted Clef-flash. This pairs the backends on identical requests.
- `wholemap.py`, `items.py`, and `offsets.py` are the three kinds of Claude request. Their drivers are `screen_wholemap.py`, `screen_items.py`, and `screen_offsets.py`.
  - Every request goes through the stage's provider, `ClaudeProvider`, with one retry of a malformed answer and a cap on the screening's requests (`claude_run.py`).
  - `pictures.py` draws a region with its grid ticked.
  - `screen_summary.py` pools the stored outcomes by model and effort.

## The files

Each file here has a counterpart for commercial maps, in the same format, under `~/.re-pod/map-plan/osr-suite/reading/specs/`.

- `maps.json` lists each map region with its key, its image, the answer-key work directory, the frame from the working grid to the key's grid, and its classifier spec.
- `windows.json` lists the screening windows: per map, the 12 by 12 windows with the most universe edges, chosen to hold every rare letter the map has. `windows-hosted.json` lists the window the hosted run read.
- `pages.json` lists the map pages and the regions on each.
- `halves.json` lists the synthetic pairs of halves, with the letter of their true join.
- `synthetic-maps.json` and `synthetic-windows.json` cover seven synthetic regions whole.

## Run it

Run from the osr-forge checkout. Each driver keeps what it has stored and asks only what's missing. Each needs the following:

- The Claude drivers need `OSRFORGE_CLAUDE_LEDGER`. They read the API key from the `ANTHROPIC_API_KEY=` line of `~/repos/re-pod/.env`, inside the process.
- The local Clef runs need a System One server, such as the maintainer's Ollama with `clef-flash`. Never start, stop, or restart it.
- The hosted Clef runs need `OSRFORGE_CLEF_ACCOUNT_ID`, `OSRFORGE_CLEF_LEDGER`, and a wrangler login. They stop at their first failure and are held to `--spend` dollars on the Clef ledger.

Add the commercial maps' files after each committed file, as in `tools/reading/maps.json $P/palace-maps.json`. In the commands, `R` is the results directory and `P` the commercial specs:

```text
R=~/.re-pod/map-plan/osr-suite/reading
P=$R/specs
```

1. The classifier's readings, and the bar:

   ```text
   uv run python -m tools.answer_keys.run tools/answer_keys/maps.json --out $R/classifier
   uv run python -m tools.reading.bar $R/classifier tools/reading/maps.json --out $R/bar.json
   ```

2. Local Clef-flash on the screening windows' edges, one option set at a time, and on the cells the ink leaves unsettled:

   ```text
   uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows.json \
       --set keyset --kind edge --backend local --workers 2 --out $R/clef
   uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows.json \
       --set seven --kind cell --backend local --out $R/clef
   ```

3. The option sets compared per style, on the edges every set answered. `keyset-stored` names the answer-key pass's own answers:

   ```text
   uv run python -m tools.reading.screen_clef tools/reading/maps.json --windows tools/reading/windows.json \
       --source keyset=keyset-stored --source seven=$R/clef/local/seven --paired --json $R/clef/sets.json
   ```

4. The stage's reading of every key edge against the classifier's, with the band per map:

   ```text
   uv run python -m tools.reading.export_stored tools/reading/maps.json --out $R/clef/stored-keyset
   uv run python -m tools.reading.screen_bar tools/reading/maps.json --windows $R/clef/stored-keyset/whole-maps.json \
       --set keyset=$R/clef/stored-keyset --classifier $R/classifier --calibration clef+ink --json $R/clef/bar.json
   ```

5. The calibrations on synthetic maps with exact answers:

   ```text
   uv run python -m tools.reading.clefask tools/reading/synthetic-maps.json \
       --windows tools/reading/synthetic-windows.json --set keyset --kind edge --backend local --workers 2 --out $R/clef
   uv run python -m tools.reading.screen_synthetic tools/reading/maps.json \
       --synthetic tools/reading/synthetic-maps.json --answers $R/clef/local/keyset --json $R/clef/synthetic.json
   ```

6. The marks, with Clef's key and letter questions:

   ```text
   uv run python -m tools.reading.screen_marks tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/clef --ask
   ```

7. The Claude grid, over models and efforts:

   ```text
   uv run python -m tools.reading.screen_wholemap tools/reading/pages.json --out $R/claude \
       --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
   uv run python -m tools.reading.screen_items tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/claude --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
   uv run python -m tools.reading.screen_items tools/reading/maps.json --pages tools/reading/pages.json \
       --out $R/claude --models claude-opus-5-5 --efforts medium --take 30 --per-request 30 --tag k30 --cap 160
   uv run python -m tools.reading.screen_offsets tools/reading/halves.json --out $R/claude \
       --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
   uv run python -m tools.reading.screen_summary $R/claude --json $R/claude/summary.json
   ```

8. The backends on identical requests:

   ```text
   uv run python -m tools.reading.hosted_exported tools/reading/maps.json --windows tools/reading/windows-hosted.json \
       --out $R/clef --model clef --workers 4 --max-requests 470 --spend 0.15
   uv run python -m tools.reading.hosted_exported tools/reading/maps.json --windows tools/reading/windows-hosted.json \
       --out $R/clef --model clef-flash --workers 4 --max-requests 470 --spend 0.15
   uv run python -m tools.reading.screen_clef tools/reading/maps.json --source local=keyset-stored \
       --source hosted=keyset-hosted-stored --paired
   ```

## The settings

The maintainer decided the following for the stage:

- **Claude model:** Opus 5.5 (`claude-opus-5-5`), for every kind of request.
- **Efforts:** high for the whole-page read, medium for uncertain items, low for the offset pick.
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

The figures pool the committed Chaotic Caves maps and the maintainer's private corpus, as counts by style. The full log, with every command and the private maps' figures, is `~/.re-pod/map-plan/osr-suite/reading/progress.md`.

The bar for walls read as open is the classifier's rate on the keys over the scorer's edge universe:

| Style | Walls read as open | Edges | Rate |
| --- | --- | --- | --- |
| TSR blue | 34 | 3,380 | 1.01% |
| Black and white | 25 | 3,307 | 0.76% |
| Dark rock | 4 | 628 | 0.64% |
| Cave | 56 | 5,339 | 1.05% |

Claude made 128 requests, and none came back malformed.

- **Whole-page reads:**
  - Every setting reads the scale and north of all 7 regions right.
  - Every setting except Opus at medium effort reads all 7 styles right.
  - Opus at high effort finds 66 of the keys' 104 symbols, including 40 of 42 stairs and 13 of 16 cave mouths. Sonnet at high effort finds 39.
- **Uncertain items,** 30 to a request on 204 items:
  - Opus at medium effort gets 198 right, Opus at high 193, Sonnet at low 172. The calibrated Clef-and-ink decision gets 155 of the same items.
  - Thirty items a request is as accurate as ten on the same items.
  - Sonnet doesn't think on item requests at any effort: it writes about 143 output tokens a request.
- **Offset picks** on two real pairs of halves: Sonnet at low and medium effort picks right 4 of 4 times over two trials. Sonnet at high effort, and Opus at every effort, pick at least one wrong offset, so under the plan's rule the halves stay apart.

The ink settles a cell (floor or not, at half floor) with at most 10 errors in each style's cells that aren't partly floor. It leaves between 8% (dark rock) and 27% (black and white) of them to Clef.

Clef, on local Clef-flash unless a backend is named:

- **The plan's seven options read doors and windows as open.**
  - On two black-and-white windows they name 2 of 20 doors and 0 of 22 windows, where the answer-key sets name 16 and 13.
  - On two TSR blue windows they read 141 of 150 walls as open.
  - Any option added to a style's set costs it its windows.
- **Doors and the ink.** A door's box breaks the drawn line, so the ink alone makes a door look open. Without doors first, a calibration with the ink reads all 19 of the synthetic black-and-white doors as open.
- **The calibration's held-out error** is 0.009 to 0.021. Fitted on the real keys alone, it reads 9 to 13 of a synthetic dark-rock map's 15 interior walls as open. With the other synthetic maps of the style in the fit, it reads none. A style with few blocked edges in its real keys needs the synthetic maps in its fit.
- **Before Claude, on every key edge,** the stage's reading with the ink reads no more walls as open than the classifier:
  - TSR blue: 16 against 34, at 0.7.
  - Black and white: 23 against 25, at 0.9.
  - Cave: 23 against 56, at 0.9.
  - Dark rock: 3 against 4, at every threshold.
- **Clef alone** misses black and white's bar at every threshold, and needs a threshold of 0.9 on TSR blue and cave.
- **The cell question** reads floor or not no better than the ink's half rule. It reads a plain grey moat as rock: 0 of 33 water cells. It names the stairs of TSR blue and black and white: 16 of 16 symbol cells.
- **Marks:**
  - The detector finds a mark within a cell of all 209 key marks.
  - Clef reads 175 of the 209 right, and 5 of 30 single letters.
  - Clef gives a key to most marks that aren't labels. Keeping each key's most probable mark leaves 166 keys right and 33 false keys among 561 other marks.
- **Backends,** on identical requests:
  - Hosted Clef-flash agrees with local Clef-flash on 96% to 98% of top answers.
  - Hosted Clef is no better than local Clef-flash on TSR blue, cave, and black and white. It is better only on dark rock's few blocked edges.
  - The free Workers AI plan allows about 1,250 requests a day, fewer than a large level's 2,000 or so edges.

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

No test calls a service or reads a ledger outside its temporary directory.
