---
name: log-stamps-from-date
description: Stamp every progress-log entry with the clock from `date` in the same command, never from an estimate
metadata:
  type: feedback
---

Write each progress entry's time from `date` run in the same command that appends the entry, and name the time of an event (a batch sent, a run started) from its artefact (the batch's `created`, a log file's time), never from a guess.

**Why:** on 2026-11-02 three entries carried stamps up to an hour ahead of the clock (19:35 when it was 18:36), because the times were estimated from how long the work felt; the lead noticed and asked for corrections, and the corrected stamps had to be rebuilt from file times and batch records.

**How to apply:** in the shell, `printf '## %s, ...' "$(date '+%Y-%m-%d %H:%M')" >> progress.md`, and when an entry mentions when something went out, read the time from the record that holds it.
