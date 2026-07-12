# Owner sampling

The human audit at the end of the truth-authoring pipeline: before a module's
scores go on the committed BYOM scoreboard, the module's owner spot-checks the
truth file against the printed module and records the result. This page
explains what that check actually tests, why it has to be a human, and exactly
how to do it. The full authoring discipline lives in
[`tools/eval/AUTHORING.md`](https://github.com/mmacy/osr-forge/blob/main/tools/eval/AUTHORING.md);
this guide is the step-by-step for its final leg.

## What you're testing — the yardstick, not the product

It's tempting to audit a conversion by comparing `adventure.json` against the
module. That's a useful check of the *pipeline*, but it is not what owner
sampling is for. Every number on a scoreboard is "extraction versus truth" —
the truth file is the measuring stick. If the stick is wrong, the published
number is a wrong claim about the extraction, and nobody reading the
scoreboard can detect it from the scoreboard.

So the sample compares exactly two things, side by side:

- the **printed module** (your PDF), and
- the **truth file** (`<your-corpus>/<module-id>/truth.yaml`).

`adventure.json`, `report.json`, and the rest of the workdir play no part in
this check.

## Why a human — the uncorrelated leg

The trust chain behind every published truth file has three legs:

1. **An independent instrument authored it** — an agent working from the
   printed pages only, with pipeline output banned from its context.
2. **An adversarial pass verified it** — a second agent, fresh context, same
   independence rules, re-checking every fact against its cited page and
   hunting for omissions.
3. **The owner sampled it** — you.

The first two legs are agents, typically from the same model family. Agents
that share a family can share blind spots: both may misread the same OCR
garble the same way, or misapply the same convention consistently. Two
agreeing agents cannot rule out correlated error — a small human sample is
the only leg outside that correlation class. That's why the sample can be
small (it exists to catch *systematic* instrument error cheaply, not to redo
the work) and why it cannot be delegated back to an agent.

The result lands in the manifest's `truth_provenance.verified` record, and
`publish` refuses a module without provenance. Publishing without the sample
would make the committed record claim a human audit that never happened.

## The bar

Spot-check **at least 10 areas or 10% of the module's keyed areas, whichever
is larger**, plus **every flagged judgment call**. Judgment calls are the
inline `# JUDGMENT:` comments the authoring agent left in the truth file —
each is one line with a page citation, and most will already carry the
adversarial pass's confirmation; the genuinely contested ones are named in
the manifest's `truth_provenance.verified` text.

## The steps

1. **Open the module PDF and the truth file side by side.** The truth file's
   header comment records which printing it was authored from and the
   printed-page-to-PDF-page offset, so you can jump straight from a truth
   entry's page cite to the right PDF page.

2. **Pick your sample areas yourself.** Any spread across the module's levels
   works. You choosing is part of the design: the agents that wrote and
   verified the file must not also choose which parts of it get audited.

3. **For each sampled area, read the printed room entry, then check its truth
   block** (blocks appear in printed-key order):

    - **Creatures.** Every creature the printed entry places in the area
      appears under `encounters`, named as the stat block prints it, with the
      right `count` wherever the module states a fixed number. No creatures
      in the printed entry → no `encounters` in the block.
    - **Treasure.** `present: true` exactly when the printed entry states
      coins, valuables, or magic items in the area — including items carried
      by its occupants, excluding rewards promised elsewhere. A block with no
      `treasure` line at all is a deliberate "not asserted" — skip it; that
      area sits outside the treasure metric by design.
    - **Connections.** Only if the block has a `connections` line: the list
      must match the *complete* set of same-level areas the map and text
      connect it to — no more, no fewer. No `connections` line means "not
      asserted" — skip it.

    ```yaml
    - key: "13"
      encounters:
        - name: stirge      # printed name, singular
          template: stirge  # the osrlib catalog id it should resolve to
          count: 6          # only because the module states exactly 6
      treasure:
        present: true       # the entry states in-area valuables
    ```

4. **Jot one line per area** — `room 17: OK` or `room 52: module says 3
   sprites, truth says 2`. Terse is fine; the notes are the audit record.

5. **Read the flagged judgment calls and answer "is this call reasonable?"**
   for each. You're not re-deriving anything — the flag states the call and
   cites the page; you're the tiebreaker on modeling questions the printed
   module leaves genuinely open (is a surface glade a keyed area? is a named
   NPC a rank variant or a catalog creature?).

6. **Record the outcome.** The sampled keys, any discrepancies, and your
   verdict on the judgment calls go into the manifest's
   `truth_provenance.verified` text. If the sample found a real discrepancy,
   fix the truth file against the printed page first, re-score the module
   (offline — the conversion is already done and cached), and only then
   publish.

## What this buys

- **Trustworthy published numbers.** The committed BYOM scoreboard's whole
  value is that outsiders can believe it. Its trust story — independent
  instrument, adversarial verification, owner sample — is only as true as its
  weakest leg, and the sample is the leg that certifies the other two
  weren't confidently wrong together.
- **Cheap detection of systematic error.** Ten areas won't catch a single
  transcribed typo, and they don't need to: they catch an instrument that
  misreads a stat-block format, misapplies a convention, or hallucinates a
  pattern — failure modes that repeat, and therefore surface in any honest
  sample.
- **An honest provenance record.** Every manifest says exactly which
  verification legs its truth actually received. The sample keeps "the owner
  checked it" a fact rather than a courtesy claim — the same
  no-retroactive-claims rule the eval corpus applies everywhere else.

See [Evals](../evals.md) for the metric families and the BYOM workflow this
feeds into.
