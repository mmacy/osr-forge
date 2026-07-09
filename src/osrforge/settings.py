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
