"""Fake answers made from the answer key, for tests and for an offline run that sends nothing.

A fake answer says what the key says, so a run on fake answers measures the run's own plumbing and the ink, and its
scores are no measurement of Clef or Claude:

- a Clef edge answer puts 0.9 on the option nearest the key's letter in the asked set (a printed open line is
  `open`, a window `wall` when the set has no window, a locked or secret door `door` when the set lacks it) and
  spreads the rest evenly
- a Clef mark answer names the key's mark within one cell whose key or letter the question offers, at 0.9, and
  otherwise answers `none`
- a whole-page read is [`key_page_answer`][tools.reading.full_reading.key_page_answer]
- an item judgment is the key's letter for the edge or cell
- an offset pick is the true letter
"""

from collections.abc import Callable, Mapping
from typing import Any

from osrforge.providers.decisions import DecisionRequest
from osrforge.providers.reading import ReadingRequest
from osrforge.tracing.truth import GeometryTruth, parse_cell

from .answers import LETTERS as OPTION_LETTERS
from .full_reading import key_page_answer, region_key_panel
from .full_store import ClefAnswer
from .items import CELL_VALUES, EDGE_VALUES
from .maps import ScreenMap, key_document
from .screen_wholemap import Page

__all__ = ["FakeClaude", "FakeClef"]

_NEAREST = {"o": "op", "p": "po", "w": "wn", "n": "nw", "d": "d", "l": "ld", "s": "sd"}


def _panel(screen: ScreenMap):  # type: ignore[no-untyped-def]
    return region_key_panel(key_document(screen.key), screen.id)


def _spread(options: list[str], chosen: str) -> dict[str, float]:
    rest = (1.0 - 0.9) / max(1, len(options) - 1)
    return {option: 0.9 if option == chosen else rest for option in options}


class FakeClef:
    """Clef answers from the answer keys."""

    def __init__(self, maps: Mapping[str, ScreenMap]) -> None:
        """Answer for maps by uid."""
        self.maps = maps

    def __call__(self, request: DecisionRequest, about: dict[str, Any]) -> ClefAnswer:
        """The key's answer to a request."""
        return ClefAnswer(self._answer(request, about), request, "fake")

    def _answer(self, request: DecisionRequest, about: dict[str, Any]) -> dict[str, float]:
        options = [option for option, _text in request.options]
        panel = _panel(self.maps[about["map"]])
        if about["kind"] == "clef-edge":
            (x, y), side = tuple(about["edge"][0]), about["edge"][1]
            letter = panel.edges.at((x, y), side)
            by_letter = {OPTION_LETTERS[option]: option for option in options if option in OPTION_LETTERS}
            for candidate in _NEAREST.get(letter, "w") + "w":
                if candidate in by_letter:
                    return _spread(options, by_letter[candidate])
            return _spread(options, options[0])
        cell = tuple(about["cell"])
        for mark in panel.marks:
            mx, my = parse_cell(mark.cell)
            if max(abs(mx - cell[0]), abs(my - cell[1])) <= 1:
                for name in (f"key:{mark.text}", f"letter:{mark.text}"):
                    if name in options:
                        return _spread(options, name)
        return _spread(options, "none")


class FakeClaude:
    """Claude answers from the answer keys."""

    def __init__(self, maps: Mapping[str, ScreenMap], pages: Mapping[str, tuple[Page, GeometryTruth]]) -> None:
        """Answer for maps by uid and pages by id."""
        self.maps = maps
        self.pages = pages

    def __call__(
        self, request: ReadingRequest, check: Callable[[dict[str, Any]], list[str]], about: dict[str, Any]
    ) -> dict[str, Any]:
        """An outcome whose answer is the key's."""
        if "page" in about:
            page, key = self.pages[about["page"]]
            answer: dict[str, Any] = key_page_answer(page, key)
        elif "pair" in about:
            answer = {"choice": about["truth"], "reason": "from the answer key"}
        else:
            panel = _panel(self.maps[about["map"]])
            values = {letter: value for value, letter in EDGE_VALUES.items()}
            cells = {letter: value for value, letter in CELL_VALUES.items()}
            answers = []
            for number, (cell, side) in enumerate(about["items"], start=1):
                x, y = cell
                if side is None:
                    answers.append({"item": number, "value": cells.get(panel.cells[y][x], "rock")})
                else:
                    answers.append({"item": number, "value": values.get(panel.edges.at((x, y), side), "wall")})
            answer = {"answers": answers}
        faults = check(answer)
        return {
            "model": request.kind.model,
            "effort": request.kind.effort,
            "attempts": [{"retry": False, "seconds": 0.0, "answer": answer, "faults": faults, "cost_usd": 0.0}],
            "answer": None if faults else answer,
            "malformed_first": bool(faults),
            "cost_usd": 0.0,
        }
