"""Generate the badge-vocabulary page from the code — the one source UIs depend on.

Runs under mkdocs-gen-files at build time. The report's `Flag` members and the
lint's `LintCheck` members with the producer's `SEVERITY` table are rendered
from their definitions, so the published vocabularies can never drift from
the enums consumers badge on.
"""

import mkdocs_gen_files

from osrforge.check import SEVERITY
from osrforge.contracts.report import Flag, LintCheck

_FLAG_MEANINGS = {
    Flag.GEOMETRY_SYNTHESIZED: "the area's grid geometry was synthesized from the connection graph, not the map",
    Flag.MONSTER_UNRESOLVED: "an encounter name resolved to no catalog template (detail: the name)",
    Flag.LOW_CONFIDENCE: "extraction self-assessed low confidence, or a module-scope field was defaulted",
    Flag.CONNECTION_AMBIGUOUS: "an extracted connection referenced no known area (detail: the reference)",
    Flag.TREASURE_UNPARSED: "a treasure string the grammar could not place (detail: the string)",
    Flag.PAGE_UNREADABLE: "a page render was blanked via `blank_page_renders` (detail: the page)",
}

_CHECK_MEANINGS = {
    LintCheck.EDGE_INVALID: "an edge-map key osrlib would silently ignore — malformed, non-canonical, or out of bounds",
    LintCheck.AREA_UNREACHABLE: "no path from any entrance reaches the area (doors of any state count as passable)",
    LintCheck.ORPHAN_CELL: "a non-area cell that renders as corridor but no path reaches it",
    LintCheck.SECRET_ONLY_ACCESS: "every path into the area passes through a secret door",
    LintCheck.TRANSITION_UNPAIRED: "stairs whose target cell has no transition back (trapdoors and chutes are exempt)",
    LintCheck.DELVE_BLOCKED: "the smoke delve's static model and the engine disagree on a step",
    LintCheck.DELVE_INCOMPLETE: "the delve ended early — battle opened or a budget ran out",
}

with mkdocs_gen_files.open("reference/vocabulary.md", "w") as page:
    page.write("# Badge vocabularies\n\n")
    page.write(
        "The enumerated vocabularies review UIs badge on, generated from their one source in the code "
        "([`Flag`][osrforge.contracts.report.Flag], [`LintCheck`][osrforge.contracts.report.LintCheck], and the "
        "producer's [`SEVERITY`][osrforge.check.SEVERITY] table). Growing a vocabulary is additive; renaming a "
        "member is a schema-version event.\n\n"
    )

    page.write("## Report flags\n\n")
    page.write(
        "Serialized as `<flag>` or `<flag>:<detail>` in `report.json`'s per-area and module-scope `flags` arrays.\n\n"
    )
    page.write("| flag | meaning |\n| --- | --- |\n")
    for flag in Flag:
        page.write(f"| `{flag.value}` | {_FLAG_MEANINGS[flag]} |\n")

    page.write("\n## Lint findings\n\n")
    page.write(
        "Emitted by `check()` into `report.json`'s `findings` array; the severity column is the producer's "
        "pinned table.\n\n"
    )
    page.write("| id | severity | meaning |\n| --- | --- | --- |\n")
    for check_id in LintCheck:
        page.write(f"| `{check_id.value}` | {SEVERITY[check_id]} | {_CHECK_MEANINGS[check_id]} |\n")
