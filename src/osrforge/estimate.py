"""Cost estimation: preprocess only, then pure arithmetic — a rough token/cost estimate with no model call.

`estimate` runs the real `preprocess()` into the given workdir — the licensing
invariant forbids persisting module text outside the user's workdir, so a temp
directory is not an option, and the workdir is warm for the human's next step
(`rerun survey` continues from the rendered pages) — then does pure arithmetic:
no provider, no model call.

The heuristics are pinned from measured behavior (the recorded capability
probes and four recorded full-module calibration runs) and kept deliberately
coarse — "rough" is the contract, and the recorded calibration band (±40% on
input tokens against the measured runs) keeps the error honest. Schema retries
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
"""The measured per-page image cost — DPI-independent (100/150/200 DPI cost identical tokens)."""

_CHARS_PER_TOKEN = 4
_SURVEY_OVERHEAD_TOKENS = 2_000  # prompt + schema
_SURVEY_OUTPUT_TOKENS_PER_PAGE = 70
# The census re-sends every survey window's pages with a much smaller prompt
# and schema; its answer (site names, level numbers, key ranges) is small and
# flat per window.
_CENSUS_OVERHEAD_TOKENS = 500
_CENSUS_OUTPUT_TOKENS_PER_WINDOW = 500
# Pages are re-sent per level batch and map pages ride every batch, so a
# send-once model undercounts (JN1 measured 1.20x, minimod 0.77x, B3 ~1.1x).
_CONTENT_INPUT_FACTOR = 1.25
_CONTENT_OUTPUT_TOKENS_PER_PAGE = 550
# The LLM resolution tier: JN1 measured 4,305/340; zero when a module resolves
# deterministically, which is unknowable in advance — the flat constant is the
# honest rough call.
_MONSTERS_INPUT_TOKENS = 5_000
_MONSTERS_OUTPUT_TOKENS = 500
# The widened stat-block pass (emit is the default): one transcription request
# per non-exact name. Names cannot be counted before extraction, so the term
# prices from page count the way the content term does. Measured means over
# the retained phase 9 sweep pair: JN1 30.5 non-exact names / 48 pages
# (0.64/page), JN2 27.5 / 54 (0.51/page) — pinned at 0.6/page; per-request
# usage from JN1's nine recorded transcription exchanges, mean 8,144 in /
# 134 out — pinned at 8,000/150.
_STATBLOCK_NAMES_PER_PAGE = 0.6
_STATBLOCK_INPUT_TOKENS_PER_REQUEST = 8_000
_STATBLOCK_OUTPUT_TOKENS_PER_REQUEST = 150


@dataclass(frozen=True)
class CostEstimate:
    """The estimate: per-stage token predictions and the USD figure hosts surface.

    Attributes:
        page_count: The source's page count.
        text_tokens: Estimated tokens in the text layers, all pages.
        image_tokens: Estimated page-image tokens, all pages.
        survey_window_count: How many requests the survey runs as — 1 at or
            under `survey_max_pages` pages, one per chunked page window above.
            The census runs over the same windows.
        survey_input_tokens: Estimated survey input, all windows.
        survey_output_tokens: Estimated survey output, all windows.
        census_input_tokens: Estimated census input, all windows — the
            survey's page-image-dominated input with a smaller overhead.
        census_output_tokens: Estimated census output, all windows.
        content_input_tokens: Estimated content-pass input, all batches.
        content_output_tokens: Estimated content output.
        monsters_input_tokens: Estimated monsters-stage input — the flat LLM
            tier plus the page-count-priced stat-block pass.
        monsters_output_tokens: Estimated monsters-stage output, same terms.
        input_tokens: The input total.
        output_tokens: The output total.
        usd: The estimated cost, with each survey and census window priced at
            the doubled tier when that window's estimated input crosses the
            272K cliff.
    """

    page_count: int
    text_tokens: int
    image_tokens: int
    survey_window_count: int
    survey_input_tokens: int
    survey_output_tokens: int
    census_input_tokens: int
    census_output_tokens: int
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
    count (at the densest measured calibration module, ~1,015 text
    tokens/page, a window over ~140 pages crosses the cliff — reachable with
    the `survey_max_pages` knob raised past its image-cap default). The
    census prices the same windows again with its smaller overhead and flat
    per-window output — its cost lever is the page images, and blindness on
    scanned modules is the wrong trade, so it re-sends the full page parts.
    The monsters term is the flat LLM-tier constant plus the widened
    stat-block pass priced from page count (the constants' provenance is
    documented where they are defined).
    """
    page_count = len(page_text_tokens)
    text_tokens = sum(page_text_tokens)
    image_tokens = IMAGE_TOKENS_PER_PAGE * page_count
    page_tokens = text_tokens + image_tokens
    windows = survey_windows(page_count, settings.survey_max_pages)
    survey_input = 0
    survey_output = 0
    census_input = 0
    census_output = 0
    windowed_usd = 0.0
    for first_page, last_page in windows:
        window_pages = last_page - first_page + 1
        window_page_tokens = sum(page_text_tokens[first_page - 1 : last_page]) + IMAGE_TOKENS_PER_PAGE * window_pages
        for leg, overhead, window_output in (
            ("survey", _SURVEY_OVERHEAD_TOKENS, _SURVEY_OUTPUT_TOKENS_PER_PAGE * window_pages),
            ("census", _CENSUS_OVERHEAD_TOKENS, _CENSUS_OUTPUT_TOKENS_PER_WINDOW),
        ):
            window_input = window_page_tokens + overhead
            window_crosses_cliff = window_input > LARGE_REQUEST_INPUT_TOKENS
            window_input_rate = LARGE_INPUT_USD_PER_TOKEN if window_crosses_cliff else INPUT_USD_PER_TOKEN
            window_output_rate = LARGE_OUTPUT_USD_PER_TOKEN if window_crosses_cliff else OUTPUT_USD_PER_TOKEN
            windowed_usd += window_input * window_input_rate + window_output * window_output_rate
            if leg == "survey":
                survey_input += window_input
                survey_output += window_output
            else:
                census_input += window_input
                census_output += window_output
    content_input = math.ceil(_CONTENT_INPUT_FACTOR * page_tokens)
    content_output = _CONTENT_OUTPUT_TOKENS_PER_PAGE * page_count
    statblock_requests = math.ceil(_STATBLOCK_NAMES_PER_PAGE * page_count)
    monsters_input = _MONSTERS_INPUT_TOKENS + statblock_requests * _STATBLOCK_INPUT_TOKENS_PER_REQUEST
    monsters_output = _MONSTERS_OUTPUT_TOKENS + statblock_requests * _STATBLOCK_OUTPUT_TOKENS_PER_REQUEST
    usd = (
        windowed_usd
        + (content_input + monsters_input) * INPUT_USD_PER_TOKEN
        + (content_output + monsters_output) * OUTPUT_USD_PER_TOKEN
    )
    return CostEstimate(
        page_count=page_count,
        text_tokens=text_tokens,
        image_tokens=image_tokens,
        survey_window_count=len(windows),
        survey_input_tokens=survey_input,
        survey_output_tokens=survey_output,
        census_input_tokens=census_input,
        census_output_tokens=census_output,
        content_input_tokens=content_input,
        content_output_tokens=content_output,
        monsters_input_tokens=monsters_input,
        monsters_output_tokens=monsters_output,
        input_tokens=survey_input + census_input + content_input + monsters_input,
        output_tokens=survey_output + census_output + content_output + monsters_output,
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

    Examples:
        ```python
        from pathlib import Path

        from osrforge import estimate

        cost = estimate(Path("module.pdf"), Path("module.forge"))
        print(f"{cost.page_count} pages, ~${cost.usd:.2f}")
        ```
    """
    effective = settings if settings is not None else ConversionSettings()
    run = preprocess(pdf_path, workdir, effective)
    workdir_files = Workdir(workdir)
    page_text_tokens = tuple(
        math.ceil(len(workdir_files.page_txt(number).read_text(encoding="utf-8")) / _CHARS_PER_TOKEN)
        for number in range(1, run.page_count + 1)
    )
    return _estimate_from_measurements(page_text_tokens, effective)
