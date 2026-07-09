"""Run metadata: the `run.json` contract.

`run.json` is operational metadata — per-stage status, timestamps, and token
usage for one conversion run. Timestamps are legal here and only here: the pure
artifacts (`adventure.json`, `report.json`, previews) must contain none, or
assembly purity's byte-stability guarantee dies.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from osrforge.settings import ConversionSettings
from osrforge.versioning import SCHEMA_VERSION, osrforge_version

__all__ = ["RunMeta", "Stage", "StageState", "StageStatus", "TokenUsage"]


class TokenUsage(BaseModel):
    """Model token consumption, as reported by the provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class Stage(StrEnum):
    """The spec's pipeline stage names, as `run.json` wire values.

    These key `run.json`'s stage table; changing one is a schema-version event.
    They do not name the `stages/` cache files — the spec's workdir layout pins
    those separately, and only the model-calling stages have caches. Geometry is
    deterministic and recomputed inside every assembly rather than cached; its
    `run.json` entry completes with assembly (reading to be confirmed by phase 2).
    """

    PREPROCESS = "preprocess"
    SURVEY = "survey"
    CONTENT = "content"
    MONSTERS = "monsters"
    GEOMETRY = "geometry"
    ASSEMBLE = "assemble"


StageState = Literal["pending", "running", "completed", "failed"]
"""One stage's lifecycle state."""


class StageStatus(BaseModel):
    """One stage's status entry in `run.json`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: StageState = "pending"
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    usage: TokenUsage | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def _utc_only(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("stage timestamps must be timezone-aware UTC")
        return value.astimezone(UTC)


class RunMeta(BaseModel):
    """The `run.json` document: source identity, settings echo, and stage table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    osrforge_version: str = Field(default_factory=osrforge_version)
    source_sha256: str
    source_bytes: int = Field(ge=0)
    page_count: int = Field(ge=0)
    settings: ConversionSettings
    provider: str | None = None
    model_id: str | None = None
    stages: dict[Stage, StageStatus]

    def with_stage(self, stage: Stage, status: StageStatus) -> RunMeta:
        """Return a copy with one stage's status replaced.

        Args:
            stage: The stage to update.
            status: Its new status entry.

        Returns:
            A new `RunMeta`; this one is unchanged.
        """
        return self.model_copy(update={"stages": {**self.stages, stage: status}})

    def with_model(self, provider: str, model_id: str) -> RunMeta:
        """Return a copy with the provider and model identity set.

        Args:
            provider: The provider class name, e.g. `FoundryProvider`.
            model_id: The model identifier the service returned.

        Returns:
            A new `RunMeta`; this one is unchanged.
        """
        return self.model_copy(update={"provider": provider, "model_id": model_id})
