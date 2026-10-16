"""Drawing that gives the same pixels on every platform: a supersampled canvas, a stroke font, noise, and encoding.

Every shape is rasterized here with numpy arithmetic that IEEE 754 rounds the
same way everywhere (addition, multiplication, division, square roots, and
comparisons), never with a font file or a library's antialiasing. A pixel's
colour comes from how many of its `SUPERSAMPLE` by `SUPERSAMPLE` sample points
each layer covers, counted in integers, so a PNG drawn on macOS and on Linux
has the same pixels. Noise comes from a hash of the pixel's position, not from
a random generator whose stream could change between library versions.

Image coordinates put pixel `(i, j)` centred on the point `(i, j)`, the
convention the registration's corners use.
"""

import io
import itertools
import math
from collections.abc import Callable, Iterable, Sequence
from typing import Literal

import numpy as np
import numpy.typing as npt
from PIL import Image

SUPERSAMPLE = 4
"""Sample points per pixel along each axis."""

type Point = tuple[float, float]
type Rgb = tuple[int, int, int]
type FloatArray = npt.NDArray[np.float64]
type BoolArray = npt.NDArray[np.bool_]
type Shape = Callable[[FloatArray, FloatArray], BoolArray]
"""A shape's membership test over broadcast sample coordinates `(x, y)` in pixels."""

Layer = Literal["ink", "tint", "paper"]
"""What a shape paints: full ink, the light tint of grid lines, or blank paper over both."""


class Canvas:
    """A two-layer drawing surface sampled `SUPERSAMPLE` times along each axis.

    The ink layer holds lines, fills, and text. The tint layer holds the light
    grid lines. Painting paper clears both, so a door's box can blank the wall
    under it.
    """

    def __init__(self, width: int, height: int) -> None:
        """Make a blank canvas.

        Args:
            width: The image width in pixels.
            height: The image height in pixels.
        """
        self.width = width
        self.height = height
        self.ink: BoolArray = np.zeros((height * SUPERSAMPLE, width * SUPERSAMPLE), dtype=np.bool_)
        self.tint: BoolArray = np.zeros((height * SUPERSAMPLE, width * SUPERSAMPLE), dtype=np.bool_)

    def paint(self, shape: Shape, box: tuple[float, float, float, float], layer: Layer = "ink") -> None:
        """Paint every sample point inside a shape.

        Args:
            shape: The membership test.
            box: The shape's bounding box `(xmin, ymin, xmax, ymax)` in pixels; samples outside it are skipped.
            layer: What to paint.
        """
        xmin, ymin, xmax, ymax = box
        columns = _sample_range(xmin, xmax, self.width)
        rows = _sample_range(ymin, ymax, self.height)
        if columns is None or rows is None:
            return
        xs = (np.arange(columns[0], columns[1], dtype=np.float64) + 0.5) / SUPERSAMPLE - 0.5
        ys = (np.arange(rows[0], rows[1], dtype=np.float64) + 0.5) / SUPERSAMPLE - 0.5
        inside = shape(xs[np.newaxis, :], ys[:, np.newaxis])
        window = (slice(rows[0], rows[1]), slice(columns[0], columns[1]))
        if layer == "ink":
            self.ink[window] |= inside
        elif layer == "tint":
            self.tint[window] |= inside
        else:
            self.ink[window] &= ~inside
            self.tint[window] &= ~inside

    def polygon(self, points: Sequence[Point], layer: Layer = "ink", pattern: Shape | None = None) -> None:
        """Fill a convex polygon, optionally only where a pattern holds.

        Args:
            points: The corners in order, either way round.
            layer: What to paint.
            pattern: A second test the sample must also pass, such as hatching.
        """
        count = len(points)
        area = sum(
            points[i][0] * points[(i + 1) % count][1] - points[(i + 1) % count][0] * points[i][1] for i in range(count)
        )
        sign = 1.0 if area > 0 else -1.0

        def shape(x: FloatArray, y: FloatArray) -> BoolArray:
            inside = np.ones(np.broadcast_shapes(x.shape, y.shape), dtype=np.bool_)
            for i in range(count):
                (x0, y0), (x1, y1) = points[i], points[(i + 1) % count]
                inside &= ((x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)) * sign >= 0.0
            if pattern is not None:
                inside &= pattern(x, y)
            return inside

        self.paint(shape, _bounds(points), layer)

    def segment(self, a: Point, b: Point, width: float, layer: Layer = "ink", cap: float | None = None) -> None:
        """Draw a straight line of a given width.

        Args:
            a: One end.
            b: The other end.
            width: The line's width in pixels.
            layer: What to paint.
            cap: How far the line runs past each end, in pixels. `None` rounds the ends instead.
        """
        half = width / 2.0
        (ax, ay), (bx, by) = a, b
        dx, dy = bx - ax, by - ay
        length = math.sqrt(dx * dx + dy * dy)
        if length == 0.0:
            self.disc(a, half, layer)
            return
        ux, uy = dx / length, dy / length
        if cap is None:

            def shape(x: FloatArray, y: FloatArray) -> BoolArray:
                along = np.clip((x - ax) * ux + (y - ay) * uy, 0.0, length)
                px, py = x - (ax + along * ux), y - (ay + along * uy)
                return px * px + py * py <= half * half

            reach = half
        else:
            extend = cap

            def shape(x: FloatArray, y: FloatArray) -> BoolArray:
                along = (x - ax) * ux + (y - ay) * uy
                across = (x - ax) * uy - (y - ay) * ux
                return (along >= -extend) & (along <= length + extend) & (np.abs(across) <= half)

            reach = half + extend
        box = (min(ax, bx) - reach, min(ay, by) - reach, max(ax, bx) + reach, max(ay, by) + reach)
        self.paint(shape, box, layer)

    def polyline(self, points: Sequence[Point], width: float, layer: Layer = "ink") -> None:
        """Draw a line through points with round joins.

        Args:
            points: The points in order.
            width: The line's width in pixels.
            layer: What to paint.
        """
        for a, b in itertools.pairwise(points):
            self.segment(a, b, width, layer)

    def disc(self, centre: Point, radius: float, layer: Layer = "ink") -> None:
        """Fill a circle.

        Args:
            centre: The centre.
            radius: The radius in pixels.
            layer: What to paint.
        """
        cx, cy = centre

        def shape(x: FloatArray, y: FloatArray) -> BoolArray:
            return (x - cx) * (x - cx) + (y - cy) * (y - cy) <= radius * radius

        self.paint(shape, (cx - radius, cy - radius, cx + radius, cy + radius), layer)

    def rectangle(self, box: tuple[float, float, float, float], layer: Layer = "ink") -> None:
        """Fill an axis-aligned rectangle.

        Args:
            box: `(xmin, ymin, xmax, ymax)` in pixels.
            layer: What to paint.
        """
        xmin, ymin, xmax, ymax = box

        def shape(x: FloatArray, y: FloatArray) -> BoolArray:
            return (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)

        self.paint(shape, box, layer)

    def outline(self, box: tuple[float, float, float, float], width: float, layer: Layer = "ink") -> None:
        """Draw an axis-aligned rectangle's border.

        Args:
            box: `(xmin, ymin, xmax, ymax)` in pixels, the border's centre line.
            width: The border's width in pixels.
            layer: What to paint.
        """
        xmin, ymin, xmax, ymax = box
        corners = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))
        for i in range(4):
            self.segment(corners[i], corners[(i + 1) % 4], width, layer, cap=width / 2.0)

    def pixels(self, paper: Rgb, tint: Rgb, ink: Rgb) -> npt.NDArray[np.int32]:
        """The image as integer RGB, each pixel the mean of its samples' colours.

        Args:
            paper: The blank page's colour.
            tint: The grid lines' colour.
            ink: The ink's colour.

        Returns:
            An array `height` by `width` by 3.
        """
        size = SUPERSAMPLE * SUPERSAMPLE
        shape = (self.height, SUPERSAMPLE, self.width, SUPERSAMPLE)
        inked = self.ink.reshape(shape).sum(axis=(1, 3), dtype=np.int32)
        tinted = (self.tint & ~self.ink).reshape(shape).sum(axis=(1, 3), dtype=np.int32)
        blank = size - inked - tinted
        channels = [(blank * paper[c] + tinted * tint[c] + inked * ink[c] + size // 2) // size for c in range(3)]
        return np.stack(channels, axis=2).astype(np.int32)


def _sample_range(low: float, high: float, size: int) -> tuple[int, int] | None:
    first = max(math.floor((low + 0.5) * SUPERSAMPLE - 0.5), 0)
    last = min(math.ceil((high + 0.5) * SUPERSAMPLE - 0.5) + 1, size * SUPERSAMPLE)
    return None if first >= last else (first, last)


def _bounds(points: Iterable[Point]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def hatching(spacing: float, width: float) -> Shape:
    """Diagonal lines running from the lower left to the upper right.

    Args:
        spacing: The distance between lines along the x axis, in pixels.
        width: Each line's width along the x axis, in pixels.

    Returns:
        The pattern test.
    """

    def pattern(x: FloatArray, y: FloatArray) -> BoolArray:
        return np.mod(x + y, spacing) < width

    return pattern


def waves(spacing: float, period: float, amplitude: float, width: float) -> Shape:
    """Horizontal zigzag lines, the way the maps show water.

    Args:
        spacing: The distance between lines, in pixels.
        period: The length of one zigzag, in pixels.
        amplitude: How far a zigzag rises, in pixels.
        width: Each line's thickness, in pixels.

    Returns:
        The pattern test.
    """

    def pattern(x: FloatArray, y: FloatArray) -> BoolArray:
        rise = amplitude * np.abs(2.0 * np.mod(x / period, 1.0) - 1.0)
        return np.abs(np.mod(y + rise, spacing) - spacing / 2.0) < width / 2.0

    return pattern


def blur(pixels: npt.NDArray[np.int32]) -> npt.NDArray[np.int32]:
    """Soften an image with a 1-2-1 kernel along each axis, in integers.

    Args:
        pixels: Integer RGB, `height` by `width` by 3.

    Returns:
        The softened image.
    """
    padded = np.pad(pixels, ((1, 1), (0, 0), (0, 0)), mode="edge")
    rows = (padded[:-2] + 2 * padded[1:-1] + padded[2:] + 2) // 4
    padded = np.pad(rows, ((0, 0), (1, 1), (0, 0)), mode="edge")
    return (padded[:, :-2] + 2 * padded[:, 1:-1] + padded[:, 2:] + 2) // 4


def _mix(values: npt.NDArray[np.uint64]) -> npt.NDArray[np.uint64]:
    """The splitmix64 finalizer, which turns consecutive integers into unrelated ones."""
    z = values + np.uint64(0x9E3779B97F4A7C15)
    z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return z ^ (z >> np.uint64(31))


def noise(width: int, height: int, seed: int, spread: int) -> npt.NDArray[np.int32]:
    """A field of small integers with a bell-shaped spread, the same on every platform.

    Each value is the sum of four integers drawn uniformly from `-spread` to
    `spread`, each taken from its own byte of a hash of the pixel's position
    and the seed.

    Args:
        width: The field's width.
        height: The field's height.
        seed: Which field.
        spread: The largest value each of the four terms can take.

    Returns:
        An array `height` by `width`.
    """
    index = np.arange(width * height, dtype=np.uint64) + np.uint64(seed) * np.uint64(1 << 32)
    hashed = _mix(index)
    total = np.zeros(width * height, dtype=np.int32)
    for byte in range(4):
        term = ((hashed >> np.uint64(8 * byte)) & np.uint64(0xFF)).astype(np.int32)
        total += term % (2 * spread + 1) - spread
    return total.reshape(height, width)


def encode(pixels: npt.NDArray[np.int32], kind: Literal["jpeg", "png"], quality: int = 80) -> bytes:
    """Encode integer RGB as an image file.

    Args:
        pixels: Integer RGB, `height` by `width` by 3, clipped to 0..255 here.
        kind: The file format.
        quality: The JPEG quality.

    Returns:
        The file's bytes.
    """
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    buffer = io.BytesIO()
    if kind == "jpeg":
        image.save(buffer, "JPEG", quality=quality, subsampling=2, optimize=False, progressive=False)
    else:
        image.save(buffer, "PNG", optimize=False, compress_level=9)
    return buffer.getvalue()


# The stroke font. Each glyph is polylines on a grid 4 units wide, with capitals from y = 0 to y = 6 and the
# baseline at y = 6. Lower-case letters stand from y = 3 to y = 6.

_O = [(1.0, 0.0), (3.0, 0.0), (4.0, 1.0), (4.0, 5.0), (3.0, 6.0), (1.0, 6.0), (0.0, 5.0), (0.0, 1.0), (1.0, 0.0)]
_P = [(0.0, 6.0), (0.0, 0.0), (3.0, 0.0), (4.0, 1.0), (4.0, 2.0), (3.0, 3.0), (0.0, 3.0)]
_BOWL = [(4.0, 3.5), (3.0, 3.0), (1.0, 3.0), (0.0, 4.0), (0.0, 5.0), (1.0, 6.0), (3.0, 6.0), (4.0, 5.5)]

GLYPHS: dict[str, list[list[tuple[float, float]]]] = {
    "A": [[(0.0, 6.0), (2.0, 0.0), (4.0, 6.0)], [(0.7, 4.0), (3.3, 4.0)]],
    "B": [
        [(0.0, 0.0), (0.0, 6.0), (3.0, 6.0), (4.0, 5.0), (4.0, 4.0), (3.0, 3.0), (0.0, 3.0)],
        [(0.0, 0.0), (3.0, 0.0), (4.0, 1.0), (4.0, 2.0), (3.0, 3.0)],
    ],
    "C": [[(4.0, 1.0), (3.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 5.0), (1.0, 6.0), (3.0, 6.0), (4.0, 5.0)]],
    "D": [[(0.0, 0.0), (0.0, 6.0), (2.5, 6.0), (4.0, 4.5), (4.0, 1.5), (2.5, 0.0), (0.0, 0.0)]],
    "E": [[(4.0, 0.0), (0.0, 0.0), (0.0, 6.0), (4.0, 6.0)], [(0.0, 3.0), (3.0, 3.0)]],
    "F": [[(4.0, 0.0), (0.0, 0.0), (0.0, 6.0)], [(0.0, 3.0), (3.0, 3.0)]],
    "G": [
        [(4.0, 1.0), (3.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.0, 5.0), (1.0, 6.0), (3.0, 6.0), (4.0, 5.0), (4.0, 3.0)],
        [(4.0, 3.0), (2.0, 3.0)],
    ],
    "H": [[(0.0, 0.0), (0.0, 6.0)], [(4.0, 0.0), (4.0, 6.0)], [(0.0, 3.0), (4.0, 3.0)]],
    "I": [[(1.0, 0.0), (3.0, 0.0)], [(2.0, 0.0), (2.0, 6.0)], [(1.0, 6.0), (3.0, 6.0)]],
    "J": [[(4.0, 0.0), (4.0, 5.0), (3.0, 6.0), (1.0, 6.0), (0.0, 5.0)]],
    "K": [[(0.0, 0.0), (0.0, 6.0)], [(4.0, 0.0), (0.0, 3.5)], [(1.3, 2.5), (4.0, 6.0)]],
    "L": [[(0.0, 0.0), (0.0, 6.0), (4.0, 6.0)]],
    "M": [[(0.0, 6.0), (0.0, 0.0), (2.0, 3.0), (4.0, 0.0), (4.0, 6.0)]],
    "N": [[(0.0, 6.0), (0.0, 0.0), (4.0, 6.0), (4.0, 0.0)]],
    "O": [_O],
    "P": [_P],
    "Q": [_O, [(2.5, 4.5), (4.0, 6.0)]],
    "R": [_P, [(2.0, 3.0), (4.0, 6.0)]],
    "S": [
        [
            (4.0, 1.0),
            (3.0, 0.0),
            (1.0, 0.0),
            (0.0, 1.0),
            (0.0, 2.0),
            (1.0, 3.0),
            (3.0, 3.0),
            (4.0, 4.0),
            (4.0, 5.0),
            (3.0, 6.0),
            (1.0, 6.0),
            (0.0, 5.0),
        ]
    ],
    "T": [[(0.0, 0.0), (4.0, 0.0)], [(2.0, 0.0), (2.0, 6.0)]],
    "U": [[(0.0, 0.0), (0.0, 5.0), (1.0, 6.0), (3.0, 6.0), (4.0, 5.0), (4.0, 0.0)]],
    "V": [[(0.0, 0.0), (2.0, 6.0), (4.0, 0.0)]],
    "W": [[(0.0, 0.0), (1.0, 6.0), (2.0, 2.0), (3.0, 6.0), (4.0, 0.0)]],
    "X": [[(0.0, 0.0), (4.0, 6.0)], [(4.0, 0.0), (0.0, 6.0)]],
    "Y": [[(0.0, 0.0), (2.0, 3.0), (4.0, 0.0)], [(2.0, 3.0), (2.0, 6.0)]],
    "Z": [[(0.0, 0.0), (4.0, 0.0), (0.0, 6.0), (4.0, 6.0)]],
    "0": [_O, [(3.5, 0.8), (0.5, 5.2)]],
    "1": [[(1.0, 1.0), (2.0, 0.0), (2.0, 6.0)], [(1.0, 6.0), (3.0, 6.0)]],
    "2": [[(0.0, 1.0), (1.0, 0.0), (3.0, 0.0), (4.0, 1.0), (4.0, 2.0), (0.0, 6.0), (4.0, 6.0)]],
    "3": [
        [(0.0, 1.0), (1.0, 0.0), (3.0, 0.0), (4.0, 1.0), (4.0, 2.0), (3.0, 3.0), (1.5, 3.0)],
        [(3.0, 3.0), (4.0, 4.0), (4.0, 5.0), (3.0, 6.0), (1.0, 6.0), (0.0, 5.0)],
    ],
    "4": [[(3.0, 6.0), (3.0, 0.0), (0.0, 4.0), (4.0, 4.0)]],
    "5": [[(4.0, 0.0), (0.0, 0.0), (0.0, 3.0), (3.0, 3.0), (4.0, 4.0), (4.0, 5.0), (3.0, 6.0), (1.0, 6.0), (0.0, 5.0)]],
    "6": [
        [
            (3.0, 0.0),
            (1.0, 0.0),
            (0.0, 1.0),
            (0.0, 5.0),
            (1.0, 6.0),
            (3.0, 6.0),
            (4.0, 5.0),
            (4.0, 4.0),
            (3.0, 3.0),
            (0.0, 3.0),
        ]
    ],
    "7": [[(0.0, 0.0), (4.0, 0.0), (1.5, 6.0)]],
    "8": [
        [(1.0, 0.0), (3.0, 0.0), (4.0, 1.0), (4.0, 2.0), (3.0, 3.0), (1.0, 3.0), (0.0, 2.0), (0.0, 1.0), (1.0, 0.0)],
        [(1.0, 3.0), (0.0, 4.0), (0.0, 5.0), (1.0, 6.0), (3.0, 6.0), (4.0, 5.0), (4.0, 4.0), (3.0, 3.0)],
    ],
    "9": [
        [
            (4.0, 3.0),
            (1.0, 3.0),
            (0.0, 2.0),
            (0.0, 1.0),
            (1.0, 0.0),
            (3.0, 0.0),
            (4.0, 1.0),
            (4.0, 5.0),
            (3.0, 6.0),
            (1.0, 6.0),
        ]
    ],
    "a": [[(4.0, 3.0), (4.0, 6.0)], _BOWL],
    "b": [
        [(0.0, 0.0), (0.0, 6.0)],
        [(0.0, 4.0), (1.0, 3.0), (3.0, 3.0), (4.0, 4.0), (4.0, 5.0), (3.0, 6.0), (1.0, 6.0), (0.0, 5.0)],
    ],
    "c": [_BOWL],
    "d": [[(4.0, 0.0), (4.0, 6.0)], _BOWL],
    "e": [[(0.0, 4.5), (4.0, 4.5), (4.0, 4.0), (3.0, 3.0), (1.0, 3.0), (0.0, 4.0), (0.0, 5.0), (1.0, 6.0), (3.5, 6.0)]],
    "-": [[(0.5, 3.0), (3.5, 3.0)]],
    "=": [[(0.5, 2.0), (3.5, 2.0)], [(0.5, 4.0), (3.5, 4.0)]],
    ":": [[(2.0, 1.8), (2.0, 2.2)], [(2.0, 4.8), (2.0, 5.2)]],
    ".": [[(2.0, 5.6), (2.0, 6.0)]],
    ",": [[(2.0, 5.4), (1.5, 7.0)]],
    "/": [[(4.0, 0.0), (0.0, 6.0)]],
    "(": [[(3.0, 0.0), (2.0, 1.0), (2.0, 5.0), (3.0, 6.0)]],
    ")": [[(1.0, 0.0), (2.0, 1.0), (2.0, 5.0), (1.0, 6.0)]],
    "'": [[(2.0, 0.0), (2.0, 1.5)]],
    " ": [],
}
"""The glyphs the maps print, as polylines."""

_ADVANCE = 5.5
"""The distance from one glyph's left edge to the next, in font units."""


def text_width(text: str, height: float) -> float:
    """How wide a line of text is drawn.

    Args:
        text: The text.
        height: The capital height in pixels.

    Returns:
        The width in pixels, from the first glyph's left edge to the last glyph's right edge.
    """
    unit = height / 6.0
    return (len(text) * _ADVANCE - (_ADVANCE - 4.0)) * unit if text else 0.0


def text(canvas: Canvas, line: str, centre: Point, height: float, weight: float, layer: Layer = "ink") -> None:
    """Draw a line of text centred on a point.

    Args:
        canvas: Where to draw.
        line: The text, in the glyphs `GLYPHS` defines.
        centre: The point the text's middle sits on: halfway across, and halfway up the capitals.
        height: The capital height in pixels.
        weight: The stroke width in pixels.
        layer: What to paint.

    Raises:
        KeyError: If the text has a character the font lacks.
    """
    unit = height / 6.0
    left = centre[0] - text_width(line, height) / 2.0
    top = centre[1] - height / 2.0
    for index, character in enumerate(line):
        origin = left + index * _ADVANCE * unit
        for stroke in GLYPHS[character]:
            points = [(origin + x * unit, top + y * unit) for x, y in stroke]
            if len(points) == 1:
                canvas.disc(points[0], weight / 2.0, layer)
            canvas.polyline(points, weight, layer)


def text_box(line: str, centre: Point, height: float, pad: float) -> tuple[float, float, float, float]:
    """The rectangle a line of text covers, with padding.

    Args:
        line: The text.
        centre: Where it's centred.
        height: The capital height in pixels.
        pad: The margin around it, in pixels.

    Returns:
        `(xmin, ymin, xmax, ymax)`.
    """
    half = text_width(line, height) / 2.0
    return (
        centre[0] - half - pad,
        centre[1] - height / 2.0 - pad,
        centre[0] + half + pad,
        centre[1] + height / 2.0 + pad,
    )
