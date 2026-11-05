"""Claude's offset pick for two halves of a map (step 4 of Reading the map), as the screening asks it.

Registration gives the row shift and three candidate column offsets, the three with the smallest gaps that aren't
negative. The request shows both halves whole, then the fold joined at each candidate offset as pictures A, B, and
C, in order of increasing gap, each a band of cells on both sides of the fold with the gap between the scans left
blank. Claude picks one, or none.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from osrforge.providers.base import ImagePart, TextPart
from osrforge.providers.reading import Effort, ReadingRequest, RequestKind

from .pictures import fit_for_api, png

__all__ = [
    "LETTERS",
    "OFFSET_SCHEMA",
    "OFFSET_SYSTEM",
    "HalfPairInput",
    "check_offset",
    "offset_kind",
    "offset_request",
]

LETTERS = ("A", "B", "C")
BAND = 8
"""Cells of each half shown beside the fold."""

OFFSET_SYSTEM = (
    "You join the two halves of a printed map for a program that traces dungeon levels onto a square grid. Answer "
    "only through the structured output.\n\n"
    "Some maps are printed across two facing pages, and each page's scan holds one half. The halves meet at the fold, "
    "the line between the pages. The scans leave a narrow strip of blank paper there, and no cell is printed on both "
    "halves. The program has lined up the rows of the two grids, and it knows the fraction of a cell the strip takes, "
    "but not how many whole cells of the map are missing at the fold: none, one, or two.\n\n"
    "Each request shows the left half and the right half whole, then three pictures, A, B, and C, of the fold joined "
    "three ways: in A the right half sits nearest, in B one cell farther right, in C two cells farther right. The "
    "blank strip between the halves in each picture is the paper the join leaves. In the right join the map runs on "
    "across the strip: walls, corridors, rooms, streets, circles and other shapes continue at the same rows and keep "
    "their widths, and the grid's columns stay one cell wide. A join that leaves a passage, a room, or a shape too "
    "narrow, too wide, or broken is wrong. Pick the letter of the right join, or none when no picture shows a join "
    "the map supports."
)

OFFSET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": [*LETTERS, "none"]},
        "reason": {"type": "string"},
    },
    "required": ["choice", "reason"],
    "additionalProperties": False,
}

OFFSET_MAX_TOKENS = 32000


def offset_kind(effort: Effort) -> RequestKind:
    """The offset pick at one effort."""
    return RequestKind(
        name="reading-offset", system=OFFSET_SYSTEM, schema=OFFSET_SCHEMA, effort=effort, max_tokens=OFFSET_MAX_TOKENS
    )


@dataclass(frozen=True)
class HalfPairInput:
    """Two halves with their grids and their alignment."""

    first: np.ndarray
    first_corners: np.ndarray
    second: np.ndarray
    second_corners: np.ndarray
    rows: int
    gaps: tuple[float, ...]


def _along(xs: np.ndarray, values: np.ndarray, x: float) -> float:
    if x <= xs[0]:
        return float(values[0] + (values[1] - values[0]) * (x - xs[0]) / (xs[1] - xs[0]))
    if x >= xs[-1]:
        return float(values[-1] + (values[-1] - values[-2]) * (x - xs[-1]) / (xs[-1] - xs[-2]))
    return float(np.interp(x, xs, values))


def join_picture(pair: HalfPairInput, gap: float, label: str) -> Image.Image:
    """The fold joined with `gap` cells of blank paper between the scans, `BAND` cells of each half beside it."""
    width1 = pair.first.shape[1]
    pitch_x = float(np.mean(pair.first_corners[:, -1, 0] - pair.first_corners[:, -2, 0]))
    right_edge = width1 - 0.5
    first_y0 = _along(pair.first_corners[0, :, 0], pair.first_corners[0, :, 1], right_edge)
    second_y0 = _along(pair.second_corners[0, :, 0], pair.second_corners[0, :, 1], -0.5)
    pitch_y = float(np.mean(np.diff(pair.first_corners[:, -1, 1])))
    dy = round(first_y0 + pair.rows * pitch_y - second_y0)
    band = round(BAND * pitch_x)
    gap_px = round(gap * pitch_x)
    left = pair.first[:, width1 - band :]
    right = pair.second[:, :band]
    top = min(0, dy)
    height = max(pair.first.shape[0], pair.second.shape[0] + dy) - top
    canvas = np.full((height + 40, band * 2 + gap_px, 3), 255, dtype=np.uint8)
    canvas[40 - top : 40 - top + left.shape[0], :band] = left
    y = 40 + dy - top
    canvas[y : y + right.shape[0], band + gap_px : band + gap_px + right.shape[1]] = right
    image = Image.fromarray(canvas)
    ImageDraw.Draw(image).text((6, 4), label, fill=(0, 0, 160), font=ImageFont.load_default(size=28))
    return image


def offset_request(kind: RequestKind, pair: HalfPairInput) -> ReadingRequest:
    """The request for one pair of halves."""
    joins = [join_picture(pair, gap, letter) for gap, letter in zip(pair.gaps, LETTERS, strict=True)]
    question: list[TextPart | ImagePart] = [
        TextPart(text="The left half:"),
        ImagePart(png=png(fit_for_api(Image.fromarray(pair.first)))),
        TextPart(text="The right half:"),
        ImagePart(png=png(fit_for_api(Image.fromarray(pair.second)))),
    ]
    for letter, image in zip(LETTERS, joins, strict=True):
        question.append(TextPart(text=f"Join {letter}:"))
        question.append(ImagePart(png=png(fit_for_api(image))))
    question.append(TextPart(text="Which join is right: A, B, C, or none?"))
    return ReadingRequest(kind=kind, shared=(), question=tuple(question))


def check_offset(answer: dict[str, Any]) -> list[str]:
    """The schema checks everything an offset answer holds."""
    return []


def letters_for(columns: Sequence[int], truth: int | None) -> str:
    """The letter of the true offset among the candidates, or `none`."""
    if truth is None or truth not in columns:
        return "none"
    return LETTERS[list(columns).index(truth)]
