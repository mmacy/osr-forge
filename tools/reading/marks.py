"""Label-sized marks from the ink (step 5 of Reading the map), and Clef's question about which key each one is.

The marks are found on the ink that is left once the long straight strokes are gone: every horizontal or vertical
run of dark pixels at least `LINE` cells long is a grid line or a wall, and it is removed, with a pixel's margin.
What is left is labelled into 8-connected strokes. A stroke between `GLYPH_MIN` and `GLYPH_MAX` cells high and at most
`GLYPH_WIDE` cells wide is a glyph. Glyphs on one line of text join into a label when the gap between them is at
most `GAP` cells and their heights overlap by half. Labels stacked one above the other, overlapping by half their
widths with at most `GAP` cells between them, join into one mark. A mark's cell is the cell under its centre.

Clef is asked about each mark on a crop of the mark with half a cell around it, scaled to about 100 pixels per
cell, with the mark boxed in red. A choice question takes at most 26 options, so the survey's keys of every level
whose outline lies within two cells of the mark go in chunks of 25, each with `none`, and the most probable key
across the chunks wins. When every chunk answers `none`, a letter question decides between 25 letters and `none`.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from osrforge.providers.decisions import ClefModel, DecisionRequest

from .clefask import _render

__all__ = [
    "GAP",
    "LINE",
    "MAX_OPTIONS",
    "FoundMark",
    "find_marks",
    "key_chunks",
    "letter_request",
    "mark_answer",
    "mark_crop",
    "mark_request",
    "pixel_to_grid",
]

LINE = 0.7
GLYPH_MIN = 0.12
GLYPH_MAX = 0.9
GLYPH_WIDE = 0.9
GAP = 0.18
MARK_CELL_PX = 100

LETTERS = tuple("abcdefghijkl") + tuple("ABCDEFGHIJKLM")
"""The letters a mark question offers: 25, with `none` the most a choice question takes."""

MAX_OPTIONS = 26
"""The most options a System One choice question takes: the local server refuses more ("criteria must contain 2-26
candidates")."""


@dataclass(frozen=True)
class FoundMark:
    """One mark: its pixel box and the grid cell under its centre."""

    box: tuple[int, int, int, int]
    """`(x0, y0, x1, y1)` in pixels, the end exclusive."""

    cell: tuple[int, int]
    glyphs: int


def _runs(mask: np.ndarray, length: int) -> np.ndarray:
    """The pixels of horizontal runs at least `length` long."""
    out = np.zeros_like(mask)
    for y in range(mask.shape[0]):
        row = mask[y]
        if not row.any():
            continue
        padded = np.concatenate(([False], row, [False]))
        changes = np.flatnonzero(padded[1:] != padded[:-1])
        for start, end in zip(changes[::2], changes[1::2], strict=True):
            if end - start >= length:
                out[y, start:end] = True
    return out


def _label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected components of a mask, as a label image (0 for background) and the count."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parent = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    previous: list[tuple[int, int, int]] = []
    for y in range(height):
        row = mask[y]
        current: list[tuple[int, int, int]] = []
        if row.any():
            padded = np.concatenate(([False], row, [False]))
            changes = np.flatnonzero(padded[1:] != padded[:-1])
            for start, end in zip(changes[::2], changes[1::2], strict=True):
                start, end = int(start), int(end)
                touching = [label for (s, e, label) in previous if s <= end and e >= start]
                if touching:
                    root = find(touching[0])
                    for other in touching[1:]:
                        other_root = find(other)
                        if other_root != root:
                            parent[max(root, other_root)] = min(root, other_root)
                            root = min(root, other_root)
                    label = root
                else:
                    label = len(parent)
                    parent.append(label)
                labels[y, start:end] = label
                current.append((start, end, label))
        previous = current
    roots = np.array([find(index) for index in range(len(parent))], dtype=np.int32)
    unique = {root: number for number, root in enumerate(sorted(set(roots[1:].tolist())), start=1)}
    mapping = np.array([0] + [unique[int(root)] for root in roots[1:]], dtype=np.int32)
    return mapping[labels], len(unique)


def pixel_to_grid(corners: np.ndarray) -> np.ndarray:
    """The least-squares affine map from pixels to grid coordinates, as a 2 by 3 matrix."""
    rows, columns = corners.shape[0] - 1, corners.shape[1] - 1
    gy, gx = np.mgrid[0 : rows + 1, 0 : columns + 1]
    a = np.column_stack([corners[..., 0].ravel(), corners[..., 1].ravel(), np.ones(corners[..., 0].size)])
    solution, *_ = np.linalg.lstsq(a, np.column_stack([gx.ravel(), gy.ravel()]), rcond=None)
    return solution.T


def find_marks(darkness: np.ndarray, fill: np.ndarray, corners: np.ndarray, dark: float = 0.5) -> list[FoundMark]:
    """The label-sized marks on a region.

    Args:
        darkness: The image's darkness, 0 to 1.
        fill: The solid areas, which hold no marks.
        corners: The region's grid corners.
        dark: The darkness at which a pixel is ink.

    Returns:
        The marks whose centre lies on the region's grid, in page order.
    """
    pitch = float(np.mean(np.diff(corners[..., 0], axis=1)) + np.mean(np.diff(corners[..., 1], axis=0))) / 2
    rows, columns = corners.shape[0] - 1, corners.shape[1] - 1
    x0, x1 = int(max(0, corners[..., 0].min() - pitch)), int(min(darkness.shape[1], corners[..., 0].max() + pitch))
    y0, y1 = int(max(0, corners[..., 1].min() - pitch)), int(min(darkness.shape[0], corners[..., 1].max() + pitch))
    ink = (darkness[y0:y1, x0:x1] >= dark) & ~fill[y0:y1, x0:x1]
    length = max(3, round(LINE * pitch))
    lines = _runs(ink, length) | _runs(ink.T, length).T
    grown = lines.copy()
    grown[1:] |= lines[:-1]
    grown[:-1] |= lines[1:]
    grown[:, 1:] |= lines[:, :-1]
    grown[:, :-1] |= lines[:, 1:]
    labels, count = _label(ink & ~grown)
    boxes = []
    for number in range(1, count + 1):
        ys, xs = np.nonzero(labels == number)
        if ys.size < 4:
            continue
        height, width = (ys.max() - ys.min() + 1) / pitch, (xs.max() - xs.min() + 1) / pitch
        if GLYPH_MIN <= height <= GLYPH_MAX and width <= GLYPH_WIDE:
            boxes.append([int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1, 1])
    gap = GAP * pitch
    boxes = _join(boxes, gap, horizontal=True)
    boxes = _join(boxes, gap, horizontal=False)
    affine = pixel_to_grid(corners)
    out = []
    for bx0, by0, bx1, by1, glyphs in boxes:
        cx, cy = (bx0 + bx1) / 2 + x0, (by0 + by1) / 2 + y0
        gx, gy = affine @ np.array([cx, cy, 1.0])
        cell = (int(np.floor(gx)), int(np.floor(gy)))
        if 0 <= cell[0] < columns and 0 <= cell[1] < rows:
            out.append(FoundMark(box=(bx0 + x0, by0 + y0, bx1 + x0, by1 + y0), cell=cell, glyphs=glyphs))
    out.sort(key=lambda mark: (mark.cell[1], mark.cell[0]))
    return out


def _join(boxes: list[list[int]], gap: float, horizontal: bool) -> list[list[int]]:
    """Join boxes on one line of text (`horizontal`) or stacked one above the other, until none joins."""
    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if horizontal:
                    overlap = min(a[3], b[3]) - max(a[1], b[1])
                    near = max(a[0], b[0]) - min(a[2], b[2]) <= gap
                    joins = near and overlap >= 0.5 * min(a[3] - a[1], b[3] - b[1])
                else:
                    overlap = min(a[2], b[2]) - max(a[0], b[0])
                    near = max(a[1], b[1]) - min(a[3], b[3]) <= gap
                    joins = near and overlap >= 0.5 * min(a[2] - a[0], b[2] - b[0])
                if joins:
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] + b[4]]
                    del boxes[j]
                    changed = True
                    break
            if changed:
                break
    return boxes


def mark_crop(rgb: np.ndarray, registration, mark: FoundMark) -> bytes:  # type: ignore[no-untyped-def]
    """The mark with half a cell around it, at about 100 pixels per cell, boxed in red."""
    corners = np.array(registration.corners, dtype=np.float64)
    affine = pixel_to_grid(corners)
    (ax, ay), (bx, by) = (
        affine @ np.array([mark.box[0], mark.box[1], 1.0]),
        affine @ np.array([mark.box[2], mark.box[3], 1.0]),
    )
    span = max(bx - ax, by - ay) + 1.0
    centre = ((ax + bx) / 2, (ay + by) / 2)
    return _render(rgb, registration, centre, span, MARK_CELL_PX, (ax - 0.08, ay - 0.08, bx + 0.08, by + 0.08))


MARK_STATE = "Crop of a printed dungeon map, magnified. A red box marks one label printed on the map."
MARK_QUESTION = "Which label is printed inside the red box?"


def key_chunks(keys: Sequence[str]) -> list[list[str]]:
    """The keys split into chunks that, with `none`, fit one choice question, in the order given."""
    unique = list(dict.fromkeys(keys))
    size = MAX_OPTIONS - 1
    return [unique[start : start + size] for start in range(0, len(unique), size)] or [[]]


def mark_request(crop: bytes, keys: Sequence[str], model: ClefModel) -> DecisionRequest:
    """One key question for a mark: which of these keys is printed in the box, or none of them."""
    options: list[tuple[str, str | None]] = [(f"key:{key}", f"The room key {key}") for key in keys]
    options.append(("none", "None of these keys: another key, a letter, a word, a symbol, or nothing"))
    if len(options) < 2:
        raise ValueError("a key question needs at least one key")
    return DecisionRequest(
        tag="mark", image=crop, state=MARK_STATE, question=MARK_QUESTION, options=tuple(options), model=model
    )


def letter_request(crop: bytes, model: ClefModel) -> DecisionRequest:
    """The letter question for a mark that no key question named: which single letter, or none."""
    options: list[tuple[str, str | None]] = [(f"letter:{letter}", f"The single letter {letter}") for letter in LETTERS]
    options.append(("none", "No single letter: a key, a word, a symbol, or nothing"))
    return DecisionRequest(
        tag="mark", image=crop, state=MARK_STATE, question=MARK_QUESTION, options=tuple(options), model=model
    )


def mark_answer(chunks: Sequence[dict[str, float]], letter: dict[str, float] | None) -> str:
    """A mark's reading from its key questions' answers and, when every one said none, its letter question's.

    The key with the highest probability across the chunks wins when it beats that chunk's `none`. Otherwise the
    letter question decides, and with no letter question the answer is `none`.
    """
    best, best_p = "none", 0.0
    for answer in chunks:
        top = max(answer, key=answer.__getitem__)
        if top != "none" and answer[top] > best_p:
            best, best_p = top, answer[top]
    if best != "none":
        return best
    if letter is None:
        return "none"
    return max(letter, key=letter.__getitem__)
