"""Cost estimation — the spec's "preprocess only; rough token/cost estimate".

`estimate` runs the real `preprocess()` into the given workdir — the licensing
invariant forbids persisting module text outside the user's workdir, so a temp
directory is not an option, and the workdir is warm for the human's next step
(`rerun survey` continues from the rendered pages) — then does pure arithmetic:
no provider, no model call.

The heuristics are pinned from measured behavior (`docs/foundry-capabilities.md`
and the four recorded full-module runs) and kept deliberately coarse — the spec
asks for "rough", and the phase 3 plan records the calibration (±40% on input
tokens against the measured runs) so the error band is honest. Schema retries
and missing-key follow-ups are real tokens no pre-call estimate can see; the
band, not the point value, is the contract.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from osrforge.preprocess import preprocess
from osrforge.settings import ConversionSettings
from osrforge.survey import survey_windows
from osrforge.workdir import Workdir

__all__ = [
    "IMAGE_TOKENS_PER_PAGE",
    "INPUT_USD_PER_TOKEN",
    "LARGE_INPUT_USD_PER_TOKEN",
    "LARGE_OUTPUT_USD_PER_TOKEN",
    "LARGE_REQUEST_INPUT_TOKENS",
    "OUTPUT_USD_PER_TOKEN",
    "CostEstimate",
    "estimate",
]

# Azure OpenAI GlobalStandard pricing as of 2026-07-09, per
# docs/foundry-capabilities.md. These constants have one home — the extraction
# runner imports them from here.
INPUT_USD_PER_TOKEN = 2.50 / 1_000_000
"""Input price at the ≤272K single-request tier."""

OUTPUT_USD_PER_TOKEN = 15.00 / 1_000_000
"""Output price at the ≤272K single-request tier."""

LARGE_INPUT_USD_PER_TOKEN = 5.00 / 1_000_000
"""Input price once a single request crosses the 272K-token cliff."""

LARGE_OUTPUT_USD_PER_TOKEN = 22.50 / 1_000_000
"""Output price once a single request crosses the 272K-token cliff."""

LARGE_REQUEST_INPUT_TOKENS = 272_000
"""The single-request input size beyond which the doubled tier applies."""

IMAGE_TOKENS_PER_PAGE = 905
"""The spike's measured per-page image cost — DPI-independent (100/150/200 DPI cost identical tokens)."""

_CHARS_PER_TOKEN = 4
_SURVEY_OVERHEAD_TOKENS = 2_000  # prompt + schema
_SURVEY_OUTPUT_TOKENS_PER_PAGE = 70
# Pages are re-sent per level batch and map pages ride every batch, so a
# send-once model undercounts (JN1 measured 1.20x, minimod 0.77x, B3 ~1.1x).
_CONTENT_INPUT_FACTOR = 1.25
_CONTENT_OUTPUT_TOKENS_PER_PAGE = 550
# JN1 measured 4,305/340; zero when a module resolves deterministically, which
# is unknowable in advance — the flat constant is the honest rough call.
_MONSTERS_INPUT_TOKENS = 5_000
_MONSTERS_OUTPUT_TOKENS = 500


@dataclass(frozen=True)
class CostEstimate:
    """The estimate: per-stage token predictions and the USD figure hosts surface.

    Attributes:
        page_count: The source's page count.
        text_tokens: Estimated tokens in the text layers, all pages.
        image_tokens: Estimated page-image tokens, all pages.
        survey_window_count: How many requests the survey runs as — 1 at or
            under `survey_max_pages` pages, one per chunked page window above.
        survey_input_tokens: Estimated survey input, all windows.
        survey_output_tokens: Estimated survey output, all windows.
        content_input_tokens: Estimated content-pass input, all batches.
        content_output_tokens: Estimated content output.
        monsters_input_tokens: The flat monsters-stage input constant.
        monsters_output_tokens: The flat monsters-stage output constant.
        input_tokens: The input total.
        output_tokens: The output total.
        usd: The estimated cost, with each survey window priced at the doubled
            tier when that window's estimated input crosses the 272K cliff.
    """

    page_count: int
    text_tokens: int
    image_tokens: int
    survey_window_count: int
    survey_input_tokens: int
    survey_output_tokens: int
    content_input_tokens: int
    content_output_tokens: int
    monsters_input_tokens: int
    monsters_output_tokens: int
    input_tokens: int
    output_tokens: int
    usd: float


def _estimate_from_measurements(page_text_tokens: Sequence[int], settings: ConversionSettings) -> CostEstimate:
    """The pure arithmetic over the measured per-page text tokens — separable for pinning the formula in tests.

    The survey prices per window: each window carries its own pages' text and
    image tokens plus the flat prompt/schema overhead, and the 272K
    tier-doubling check applies per window — the chunk size caps only the
    image half of a window's tokens, while text tokens are unbounded by page
    count (the phase 3 calibration table's B3 row, ~1,015 text tokens/page,
    puts a 150-page window over the cliff).
    """
    page_count = len(page_text_tokens)
    text_tokens = sum(page_text_tokens)
    image_tokens = IMAGE_TOKENS_PER_PAGE * page_count
    page_tokens = text_tokens + image_tokens
    windows = survey_windows(page_count, settings.survey_max_pages)
    survey_input = 0
    survey_output = 0
    survey_usd = 0.0
    for first_page, last_page in windows:
        window_pages = last_page - first_page + 1
        window_input = (
            sum(page_text_tokens[first_page - 1 : last_page])
            + IMAGE_TOKENS_PER_PAGE * window_pages
            + _SURVEY_OVERHEAD_TOKENS
        )
        window_output = _SURVEY_OUTPUT_TOKENS_PER_PAGE * window_pages
        window_crosses_cliff = window_input > LARGE_REQUEST_INPUT_TOKENS
        window_input_rate = LARGE_INPUT_USD_PER_TOKEN if window_crosses_cliff else INPUT_USD_PER_TOKEN
        window_output_rate = LARGE_OUTPUT_USD_PER_TOKEN if window_crosses_cliff else OUTPUT_USD_PER_TOKEN
        survey_usd += window_input * window_input_rate + window_output * window_output_rate
        survey_input += window_input
        survey_output += window_output
    content_input = math.ceil(_CONTENT_INPUT_FACTOR * page_tokens)
    content_output = _CONTENT_OUTPUT_TOKENS_PER_PAGE * page_count
    usd = (
        survey_usd
        + (content_input + _MONSTERS_INPUT_TOKENS) * INPUT_USD_PER_TOKEN
        + (content_output + _MONSTERS_OUTPUT_TOKENS) * OUTPUT_USD_PER_TOKEN
    )
    return CostEstimate(
        page_count=page_count,
        text_tokens=text_tokens,
        image_tokens=image_tokens,
        survey_window_count=len(windows),
        survey_input_tokens=survey_input,
        survey_output_tokens=survey_output,
        content_input_tokens=content_input,
        content_output_tokens=content_output,
        monsters_input_tokens=_MONSTERS_INPUT_TOKENS,
        monsters_output_tokens=_MONSTERS_OUTPUT_TOKENS,
        input_tokens=survey_input + content_input + _MONSTERS_INPUT_TOKENS,
        output_tokens=survey_output + content_output + _MONSTERS_OUTPUT_TOKENS,
        usd=usd,
    )


def estimate(pdf_path: Path, workdir: Path, settings: ConversionSettings | None = None) -> CostEstimate:
    """Price a conversion before any model call: preprocess, then pinned heuristics.

    Args:
        pdf_path: The source module PDF.
        workdir: The workdir root to create or rebuild — preprocess output must
            land in the user's workdir (the licensing invariant), and it is
            warm for the next step.
        settings: Pipeline settings; defaults to `ConversionSettings()`.

    Returns:
        The estimate.

    Raises:
        PdfError: If preprocessing rejects the source.
    """
    effective = settings if settings is not None else ConversionSettings()
    run = preprocess(pdf_path, workdir, effective)
    workdir_files = Workdir(workdir)
    page_text_tokens = tuple(
        math.ceil(len(workdir_files.page_txt(number).read_text(encoding="utf-8")) / _CHARS_PER_TOKEN)
        for number in range(1, run.page_count + 1)
    )
    return _estimate_from_measurements(page_text_tokens, effective)
