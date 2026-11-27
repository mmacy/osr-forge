"""The full run's settings: what the screening chose and the models the maintainer chose.

A settings file is JSON with these parts (the maintainer's copy lives with the results, outside the repository):

- `claude`: `whole_page`, `items`, and `offset_pick`, each with its `model` and `effort`, `items` also with
  `items_per_request`, the Claude `ledger`, and the request `cap` its `reading-` entries count against
- `clef`: the `backend` (`local` or `hosted`), its `model`, and `cloud_fallback`, the hosted backend used when the
  local one isn't, with the most dollars it may add to the Clef ledger, `spend_usd`
- `decision`: the per-style `passable_threshold` and `epsilon`
- `milestone`: the levels milestone 1 looks at, each with its `member`, `region`, `dungeon`, and `level`

Every other part of the file records a choice in words for the reader, and the run doesn't read it.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from osrforge.providers.reading import Effort

__all__ = ["ClaudeSetting", "FullRunSettings", "load_settings"]


@dataclass(frozen=True)
class ClaudeSetting:
    """One kind of Claude request's model and effort."""

    model: str
    effort: Effort


@dataclass(frozen=True)
class FullRunSettings:
    """What the full run reads with."""

    whole_page: ClaudeSetting
    items: ClaudeSetting
    items_per_request: int
    offset_pick: ClaudeSetting
    cap: int
    ledger: Path | None
    """The Claude ledger the cap counts on. A live run refuses to start when the provider would write elsewhere."""

    clef_backend: Literal["local", "hosted"]
    clef_model: str
    hosted_model: str
    hosted_spend_usd: float
    thresholds: dict[str, float]
    epsilon: float
    milestone: tuple[tuple[str, str, str, int], ...] = ()
    """The levels milestone 1 looks at: `(member, region, dungeon, level)`."""


def _claude(part: dict[str, object]) -> ClaudeSetting:
    return ClaudeSetting(model=str(part["model"]), effort=cast(Effort, part["effort"]))


def load_settings(path: Path) -> FullRunSettings:
    """Read a settings file.

    Args:
        path: The JSON file.

    Returns:
        The settings.

    Raises:
        ValueError: If a required part is missing or out of range.
    """
    document = json.loads(path.expanduser().read_text())
    claude, clef, decision = document["claude"], document["clef"], document["decision"]
    fallback = clef.get("cloud_fallback", {})
    settings = FullRunSettings(
        whole_page=_claude(claude["whole_page"]),
        items=_claude(claude["items"]),
        items_per_request=int(claude["items"]["items_per_request"]),
        offset_pick=_claude(claude["offset_pick"]),
        cap=int(claude["cap"]),
        ledger=Path(claude["ledger"]).expanduser() if claude.get("ledger") else None,
        clef_backend=clef["backend"],
        clef_model=clef["model"],
        hosted_model=fallback.get("model", clef["model"]),
        hosted_spend_usd=float(fallback.get("spend_usd", 0.0)),
        thresholds={style: float(value) for style, value in decision["passable_threshold"].items()},
        epsilon=float(decision["epsilon"]),
        milestone=tuple(
            (str(item["member"]), str(item["region"]), str(item["dungeon"]), int(item["level"]))
            for item in document.get("milestone", ())
        ),
    )
    if settings.clef_backend not in ("local", "hosted"):
        raise ValueError(f"the Clef backend is local or hosted, not {settings.clef_backend!r}")
    if not 0.0 < settings.epsilon < 0.5:
        raise ValueError(f"epsilon lies between 0 and one half, not {settings.epsilon}")
    if settings.items_per_request < 1 or settings.cap < 0:
        raise ValueError("items_per_request is at least 1 and the cap isn't negative")
    return settings
