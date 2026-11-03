"""The owner's sample of an answer key: the calls to check, and the verdicts given on them.

The answer-key pipeline writes a *sample file*, a [`SampleSet`][tools.answer_keys.sample.SampleSet] in JSON, beside
the crops it names. The sample app (`tools.answer_keys.sample_app`) shows each call and records the owner's
verdicts in a *verdicts file*, a [`Verdicts`][tools.answer_keys.sample.Verdicts] in JSON, next to the sample file.
A commercial map's sample, crops, and verdicts stay on the owner's machine, under `~/.re-pod/` or the private
corpus, and never enter the repository.
"""

from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["CROP_TYPES", "Reader", "Reading", "SampleCall", "SampleSet", "Verdict", "Verdicts", "verdicts_path"]

CROP_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
"""The content type of each crop extension, matched without regard to case."""

Reader = Literal["claude", "classifier", "clef"]
"""The three readings an answer key settles between."""


class Reading(BaseModel):
    """One reader's value for a call's item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reader: Reader
    value: str = Field(min_length=1)
    """The reader's answer in the truth format's letters or words: a cell letter, an edge letter, or a key's cell."""


class SampleCall(BaseModel):
    """One settled call for the owner to check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    """Unique within the sample."""

    member: str = Field(min_length=1)
    """The corpus member, such as `jn1-chaotic-caves`."""

    map: str = Field(min_length=1)
    """The map image the call is on, such as `p038.png`."""

    panel: str = Field(min_length=1)
    """The id of the panel the item is on, which tells apart several panels or levels drawn on one map."""

    style: str = Field(min_length=1)
    """The map style, such as `tsr_blue`."""

    kind: Literal["cell", "edge", "key_placement"]
    """What the call decided: a cell's letter, an edge's letter, or where a key is placed."""

    category: Literal["cave", "diagonal", "key", "key_placement"]
    """Which part of the sample the call was drawn from: an item in a cave, on a diagonal, or around a key, or a key
    placement the settler decided. When the owner finds an error, every call of the same category on the same map
    goes back for a full recheck."""

    item: str = Field(min_length=1)
    """The item in its panel's coordinates: a cell `x,y`, an edge `x,y:side`, or a key's label."""

    zoom: str
    """The zoomed crop of the item, as a path relative to the sample file's directory."""

    wide: str
    """A wider crop around the item, as a path relative to the sample file's directory."""

    readings: tuple[Reading, ...] = Field(min_length=1)
    """The readings that disagreed, or that the adversarial pass checked."""

    settled: str = Field(min_length=1)
    """The settler's value, which the answer key records."""

    adversarial: str | None = None
    """The adversarial pass's value when it checked this call, or `None`."""

    reason: str = ""
    """The settler's stated reason, shown to the owner."""

    @field_validator("zoom", "wide")
    @classmethod
    def _relative_inside(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
            raise ValueError(f"a crop path is relative to the sample file and stays beside it, not {value!r}")
        if path.suffix.lower() not in CROP_TYPES:
            raise ValueError(f"a crop is a PNG or JPEG image, not {value!r}")
        return value


class SampleSet(BaseModel):
    """The calls of one sample, in the order the owner sees them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: tuple[SampleCall, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> SampleSet:
        ids = [call.id for call in self.calls]
        if len(set(ids)) != len(ids):
            raise ValueError("every call's id is unique within the sample")
        return self


class Verdict(BaseModel):
    """The owner's verdict on one call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call: str = Field(min_length=1)
    """The call's id."""

    settled: str = Field(min_length=1)
    """The settled value the owner judged, so a verdict can't silently carry over to a changed call."""

    verdict: Literal["right", "wrong", "unsure"]
    """Whether the settled value is right."""

    note: str = ""
    """What the owner wrote about it, if anything."""


class Verdicts(BaseModel):
    """Every verdict given on one sample, at most one per call, in the sample's order of calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdicts: tuple[Verdict, ...] = ()

    @model_validator(mode="after")
    def _one_per_call(self) -> Verdicts:
        calls = [verdict.call for verdict in self.verdicts]
        if len(set(calls)) != len(calls):
            raise ValueError("a call has at most one verdict")
        return self


def verdicts_path(sample: Path) -> Path:
    """The verdicts file for a sample file: `<stem>.verdicts.json` in the same directory.

    Args:
        sample: The sample file.

    Returns:
        The verdicts file's path, whether or not it exists yet.
    """
    return sample.with_name(f"{sample.stem}.verdicts.json")
