# The reading measurement

The tracing stage reads each printed map with Clef, a deterministic ink measurement, and Claude (see `docs/plans/map-tracing.md` in re-pod, "Reading the map" and step 8 of "Measurements before the contracts"). This tool measures how well that reading would score against the answer keys, with the scorer's own code, before the stage exists. Its first part, the screening, chooses the stage's settings: the bar for walls read as open, the calibration and its thresholds, how Clef is asked, how the ink settles cells, how keys are found, Claude's model and effort for each kind of request, and the Clef backend.

Commercial maps' specs, answers, and results stay outside the repository, under `~/.re-pod/map-plan/osr-suite/reading/`. The files here cover the committed Chaotic Caves maps.

## What each part measures

- `bar.py`: the bar for walls read as open, which is the classifier's rate on the answer keys per style. Each classifier reading is scored with `score_trace` on a one-panel document (`traces.py`). The rate is walls read as open over the scorer's edge universe, pooled over a style's maps.
- `ink.py` and `marks.py`: the stage's ink measurements. They share no code with the answer-key classifier.
  - `ink.py` gives each cell's floor fraction, the line across each edge (`width`, `heavy`, `line`), and the cells the ink leaves unsettled.
  - `marks.py` finds the label-sized marks.
- `clefask.py`: the stage's Clef questions.
  - An edge crop is 2 by 2 cells at 128 pixels per cell. A cell crop is 3 by 3 cells at 96 pixels per cell. Each crop is resampled through the registration and has a red box on the item.
  - Two edge option sets are screened. `keyset` is the answer-key pass's sets. `seven` is the plan's seven options, described in each style's terms.
  - Answers go to resumable JSON-lines stores on the key's grid.
- `calibrate.py` and `dataset.py`: the calibration, a ridge logistic model per style on Clef's passable log-odds (`clef`), the ink's line measurements (`ink`), or both (`clef+ink`). Each map is scored with a model fitted without it: one map left out where the style has several, spatial folds of 12 by 12 windows where it has one.
- `screen_clef.py`: compares option sets and backends per style.
  - Passable or blocked on the edges between two floor cells: raw, and calibrated at each threshold.
  - The share of answers before the first wrong one, ranked by confidence.
  - Each letter's recall over the universe.
- `screen_bar.py` and `stage_reading.py`: the stage's reading before Claude, scored against the classifier's reading of the same windows with the scorer. A cell is the ink's, or Clef's where the ink leaves it unsettled. An edge between floor cells takes the calibrated decision, and an edge against rock is a wall or the door Clef reads. The script also counts the edges in the band between ε and 1 - ε that Claude would judge, and the errors among them.
- `wholemap.py`, `items.py`, `offsets.py`, and their drivers `screen_wholemap.py`, `screen_items.py`, and `screen_offsets.py`: the three kinds of Claude request.
  - Requests go through the stage's provider, `ClaudeProvider`, with one retry of a malformed answer and a cap on the screening's requests (`claude_run.py`).
  - `screen_summary.py` pools the stored outcomes by model and effort.
- `screen_marks.py`: finds the marks, asks Clef which key each is, and scores both against the key's marks.

## Run it

From the osr-forge checkout. Each driver keeps what it has stored and asks only what's missing. The Claude drivers need `OSRFORGE_CLAUDE_LEDGER`, and they read the API key from the `ANTHROPIC_API_KEY=` line of `~/repos/re-pod/.env`. The hosted Clef run needs `OSRFORGE_CLEF_ACCOUNT_ID`, `OSRFORGE_CLEF_LEDGER`, and a wrangler login. The local Clef runs need a System One server, such as Ollama with `clef-flash`.

```text
R=~/.re-pod/map-plan/osr-suite/reading
uv run python -m tools.answer_keys.run tools/answer_keys/maps.json --out $R/classifier
uv run python -m tools.reading.bar $R/classifier tools/reading/maps.json --out $R/bar.json
uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows.json \
    --set seven --kind edge --backend local --workers 2 --out $R/clef
uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows.json \
    --set seven --kind cell --backend local --out $R/clef
uv run python -m tools.reading.screen_clef tools/reading/maps.json --source seven=$R/clef/local/seven \
    --source keyset=keyset-stored --windows tools/reading/windows.json --paired
uv run python -m tools.reading.screen_bar tools/reading/maps.json --windows tools/reading/windows.json \
    --set seven=$R/clef/local/seven --classifier $R/classifier
uv run python -m tools.reading.screen_wholemap tools/reading/pages.json --out $R/claude \
    --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
uv run python -m tools.reading.screen_items tools/reading/maps.json --pages tools/reading/pages.json \
    --out $R/claude --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
uv run python -m tools.reading.screen_offsets tools/reading/halves.json --out $R/claude \
    --models claude-sonnet-5-5 claude-opus-5-5 --efforts low medium high --cap 160
uv run python -m tools.reading.screen_marks tools/reading/maps.json --pages tools/reading/pages.json \
    --out $R/clef --ask
uv run python -m tools.reading.screen_summary $R/claude --json $R/claude/summary.json
```

The hosted run reads one window per style (`windows-hosted.json`), stops at its first failure, and is held to `--spend` dollars on the Clef ledger and `--max-requests` requests:

```text
uv run python -m tools.reading.clefask tools/reading/maps.json --windows tools/reading/windows-hosted.json \
    --set seven --kind edge --backend hosted --model clef --workers 4 --max-requests 1100 --spend 0.4 --out $R/clef
```

## What the screening found

The figures pool the committed Chaotic Caves maps and the maintainer's private corpus; counts only, by style. The full log, with every command and the private maps' figures, is `~/.re-pod/map-plan/osr-suite/reading/progress.md`.

The bar for walls read as open, the classifier's rate on the keys over the scorer's edge universe: TSR blue 34 of 3,380 edges (1.01%), black and white 25 of 3,307 (0.76%), dark rock 4 of 628 (0.64%), cave 56 of 5,339 (1.05%).

Claude, over 128 requests with no malformed answer:

- Whole-page reads: every setting reads the scale and north of all 7 regions right, and every setting but Opus at medium effort reads all 7 styles right. Opus at high effort finds 66 of the keys' 104 symbols, 40 of 42 stairs and 13 of 16 cave mouths among them; Sonnet at high effort finds 39.
- Judgments of uncertain items, 30 to a request on 204 items: Opus at medium effort 198 right, Opus at high 193, Sonnet at low 172, against 155 for the calibrated Clef-and-ink decision on the same items. Thirty items a request is as accurate as ten on the same items.
- Sonnet writes no thinking on item requests at any effort, about 143 output tokens a request.
- Offset picks on two real pairs of halves: Sonnet at low and medium effort 4 of 4 over two trials; Sonnet at high effort and Opus at every effort pick at least one wrong offset.

The ink settles a cell (floor, or not, at half floor) with at most 10 errors in each style's cells that aren't partly floor, and leaves 8% (dark rock) to 27% (black and white) of them to Clef. Clef-flash reads a plain grey moat as rock, not water.

## Tests

`tests/` runs offline on the committed Chaotic Caves key and images, and on made-up answers. It covers the following:

- The scoring: each key scores perfect against itself, and one changed edge counts as exactly one error.
- The crops' sizes and red boxes, and the store's resume.
- Answers moved onto the key's grid by a frame.
- The calibration, the ranked share, and the ink on the manor's upper floor.
- The whole-page check and score on an answer made from the key, and the item check.
- The retry of a malformed answer with its faults, and the request cap.
- The join pictures, and the marks found on the upper floor.

No test calls a service or touches the real ledgers.
