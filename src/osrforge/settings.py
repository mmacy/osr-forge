"""Pipeline settings.

Later knobs are added by the phases that consume them — an unread setting is
dead accommodation.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ConversionSettings"]


class ConversionSettings(BaseModel):
    """Deterministic pipeline knobs with the spec's defaults.

    The page and size caps are guardrails against wrong-file mistakes, not real
    constraints — they let a host surface "this file is not a module" before any
    work happens. The full `model_dump` of this model is echoed into `run.json`
    so `rerun` (phase 3) can detect settings drift between stages.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    render_dpi: int = Field(default=150, ge=1)
    """Page-render resolution in dots per inch."""

    max_pages: int = Field(default=200, ge=1)
    """Maximum source page count — roughly 3x the largest plausible B/X module."""

    max_source_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    """Maximum source file size in bytes (100 MiB)."""

    blank_page_renders: tuple[Annotated[int, Field(ge=1)], ...] = ()
    """Page numbers whose renders are emitted as blank white PNGs (text layer still extracted).

    The manual workaround the B3 verification run proved out: Azure's image
    content-safety filter can reject a page's render wholesale, and blanking
    that one render lets the conversion proceed on its text layer. Preprocess
    rejects page numbers beyond the source's page count
    ([`PdfError`][osrforge.errors.PdfError] — a reference to page 99 of a
    48-page module is a wrong-file-shaped mistake), and assembly flags each
    blanked page `page_unreadable` in the report.
    """

    content_batch_pages: int = Field(default=8, ge=2)
    """Content-pass batch size in pages.

    The default comes from the spike's 8-page batches (~16K input) producing
    clean per-area output. The floor is 2: the content planner's sliding-window
    stride is `content_batch_pages - 1`, so a floor of 1 would stall the
    planner on the same page forever.
    """

    survey_max_pages: int = Field(default=150, ge=1)
    """The single-request survey guard.

    Per `docs/foundry-capabilities.md`, beyond ~150 pages the whole-module
    survey request crosses the 272K-token pricing cliff. Survey chunking past
    this guard is phase 4's; until then a larger source raises
    [`ExtractionError`][osrforge.errors.ExtractionError].
    """

    monster_fuzzy_threshold: float = Field(default=0.85, gt=0.0, le=1.0)
    """The fuzzy tier's auto-accept floor.

    Pinned against measured catalog pairs, not taste: it accepts the observed
    true matches `normal man` → `Normal Human` (0.909) and `yellow mold` →
    `Yellow Mould` (0.957) and rejects the observed false neighbours
    `giant bee` → `Giant Bat` (0.778) and `gray jelly` → `Ochre Jelly` (0.667).
    The bar is high because a false accept silently substitutes the wrong
    monster into a playable draft, while a false reject merely routes the name
    to the LLM tier, which sees the right candidates anyway.
    """

    monster_llm_top_k: int = Field(default=8, ge=1)
    """Candidate templates offered per name in the monster-resolution LLM tier."""

    unresolved_fallback: Literal["best-effort", "omit"] = "best-effort"
    """What assembly puts in the draft where resolution or parsing came up empty.

    `best-effort` substitutes a flagged, level-appropriate monster stand-in from
    osrlib's shipped encounter tables and an unguarded-treasure roll; `omit`
    leaves the gap. The default is a pinned project decision (issue #6): a
    nerfed dungeon plays worse than approximate content, and every stand-in
    stays flagged and overridable, so review rigor is unchanged.
    """
