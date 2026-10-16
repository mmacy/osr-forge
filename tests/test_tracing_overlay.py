"""The overlay renderer: each edge kind in its colour on the registered grid, labels, tints, and byte stability.

Chunk overlay depends on the truth format alone and can run alongside the other chunks.
"""

import io

import pytest
from PIL import Image

from osrforge.tracing.overlay import OVERLAY_COLORS, render_overlay
from osrforge.tracing.truth import GeometryTruth
from tracing_support import demo_truth

pytestmark = pytest.mark.xfail(reason="chunk: overlay", strict=True)

WHITE = (255, 255, 255)
LINE_COLORS = {OVERLAY_COLORS[name] for name in ("wall", "window", "printed_line", "door", "locked", "secret")}


def blank() -> Image.Image:
    return Image.new("RGB", (200, 200), WHITE)


def colors_near(image: Image.Image, point: tuple[float, float], radius: int = 3) -> set[tuple[int, int, int]]:
    x0, y0 = round(point[0]), round(point[1])
    found: set[tuple[int, int, int]] = set()
    for x in range(x0 - radius, x0 + radius + 1):
        for y in range(y0 - radius, y0 + radius + 1):
            pixel = image.getpixel((x, y))
            assert isinstance(pixel, tuple)
            found.add((pixel[0], pixel[1], pixel[2]))
    return found


def midpoint(truth: GeometryTruth, cell: tuple[int, int], side: str) -> tuple[float, float]:
    registration = truth.region("r1").registration
    assert registration is not None
    x, y = cell
    offsets = {"north": (0.5, 0.0), "south": (0.5, 1.0), "west": (0.0, 0.5), "east": (1.0, 0.5)}[side]
    return registration.pixel((x + offsets[0], y + offsets[1]))


def test_edges_are_drawn_in_their_colours_through_the_registration():
    truth = demo_truth()
    overlay = render_overlay(blank(), truth, "r1")
    assert overlay.size == (200, 200)
    assert OVERLAY_COLORS["wall"] in colors_near(overlay, midpoint(truth, (1, 1), "west"))
    assert OVERLAY_COLORS["door"] in colors_near(overlay, midpoint(truth, (2, 2), "south"))
    assert OVERLAY_COLORS["secret"] in colors_near(overlay, midpoint(truth, (1, 2), "south"))
    assert OVERLAY_COLORS["locked"] in colors_near(overlay, midpoint(truth, (2, 1), "east"))
    assert not LINE_COLORS & colors_near(overlay, midpoint(truth, (1, 1), "east"))
    assert not LINE_COLORS & colors_near(overlay, midpoint(truth, (0, 3), "west"))


def test_cells_are_tinted_and_rock_is_left_alone():
    truth = demo_truth()
    overlay = render_overlay(blank(), truth, "r1")
    registration = truth.region("r1").registration
    assert registration is not None
    floor_centre = registration.pixel((1.5, 3.5))
    rock_centre = registration.pixel((0.5, 0.5))
    assert colors_near(overlay, floor_centre, 1) != {WHITE}
    assert colors_near(overlay, rock_centre, 1) == {WHITE}


def test_marks_and_area_keys_are_drawn():
    truth = demo_truth()
    overlay = render_overlay(blank(), truth, "r1")
    registration = truth.region("r1").registration
    assert registration is not None
    assert OVERLAY_COLORS["label"] in colors_near(overlay, registration.pixel((1.5, 1.5)), 8)
    assert OVERLAY_COLORS["label"] in colors_near(overlay, registration.pixel((4.5, 2.5)), 8)
    assert OVERLAY_COLORS["entrance"] in colors_near(overlay, registration.pixel((0.5, 3.5)), 10)
    assert OVERLAY_COLORS["transition"] in colors_near(overlay, registration.pixel((2.5, 1.5)), 10)
    assert OVERLAY_COLORS["passage"] in colors_near(overlay, midpoint(truth, (4, 1), "east"), 4)


def test_input_is_unchanged_and_output_is_byte_stable():
    truth = demo_truth()
    source = blank()
    first = render_overlay(source, truth, "r1")
    second = render_overlay(source, truth, "r1")
    assert source.tobytes() == blank().tobytes()
    assert first.mode == source.mode

    def png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    assert png(first) == png(second)


def test_unknown_or_unregistered_region_is_refused():
    truth = demo_truth()
    with pytest.raises(KeyError):
        render_overlay(blank(), truth, "nope")
    region = truth.region("r2")
    unregistered = region.model_copy(update={"verdict": "unregistered", "reason": "no grid", "registration": None})
    regions = tuple(unregistered if candidate.id == "r2" else candidate for candidate in truth.regions)
    with pytest.raises(ValueError):
        render_overlay(
            blank(), GeometryTruth.model_validate(truth.model_copy(update={"regions": regions}).model_dump()), "r2"
        )
