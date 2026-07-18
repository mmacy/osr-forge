"""Pipeline settings.

A knob is added by the code that reads it — an unread setting is dead
accommodation.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ConversionSettings"]


class ConversionSettings(BaseModel):
    """The deterministic pipeline knobs and their pinned defaults.

    The page and size caps are guardrails against wrong-file mistakes, not real
    constraints — they let a host surface "this file is not a module" before any
    work happens. The full `model_dump` of this model is echoed into `run.json`
    so [`rerun`][osrforge.convert.rerun] can detect settings drift between
    stages (the [drift guard][drift-guard]).
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

    The manual workaround a live verification run proved out: Azure's image
    content-safety filter can reject a page's render wholesale, and blanking
    that one render lets the conversion proceed on its text layer. Preprocess
    rejects page numbers beyond the source's page count
    ([`PdfError`][osrforge.errors.PdfError] — a reference to page 99 of a
    48-page module is a wrong-file-shaped mistake), and assembly flags each
    blanked page `page_unreadable` in the report.
    """

    content_batch_pages: int = Field(default=8, ge=2)
    """Content-pass batch size in pages.

    The default comes from measured 8-page batches (~16K input) producing
    clean per-area output during capability probing. The floor is 2: the content planner's sliding-window
    stride is `content_batch_pages - 1`, so a floor of 1 would stall the
    planner on the same page forever.
    """

    survey_max_pages: int = Field(default=50, ge=1)
    """The survey chunk size in pages.

    A source at or under this many pages surveys in one request; a larger
    source surveys in contiguous page windows of this size, merged before
    normalization. The default is the service's measured per-request image
    cap: the deployment rejects requests carrying more than 50 images
    (observed live on a 54-page module), a far lower bound than the
    272K-token pricing cliff that shaped the original 150-page guard. A
    50-page window also sits comfortably under that cliff at any plausible
    text density.
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

    custom_monsters: Literal["emit", "off"] = "emit"
    """Whether the monsters stage runs the stat-block pass that feeds custom-template emission.

    `emit` runs one extraction request per name the four resolution tiers left
    unresolved, caching each printed stat block for assembly's deterministic
    template mapping; `off` skips the pass (and thereby emission) — a policy
    and cost control for keeping a draft SRD-catalog-pure or skipping the
    per-unresolved-name model spend, symmetric in intent with
    `unresolved_fallback`. Owned by the monsters stage and only by it: assembly
    is driven purely by cache contents, so toggling the knob re-runs monsters —
    including its LLM resolution tier.
    """

    unresolved_fallback: Literal["best-effort", "omit"] = "best-effort"
    """What assembly puts in the draft where resolution or parsing came up empty.

    `best-effort` substitutes a flagged, level-appropriate monster stand-in from
    osrlib's shipped encounter tables and an unguarded-treasure roll; `omit`
    leaves the gap. The default is a pinned project decision (issue #6): a
    nerfed dungeon plays worse than approximate content, and every stand-in
    stays flagged and overridable, so review rigor is unchanged.
    """
