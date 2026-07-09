"""Pipeline settings.

Later knobs (content-pass batch size, fuzzy threshold, top-k) are added by the
phases that consume them — an unread setting is dead accommodation.
"""

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
