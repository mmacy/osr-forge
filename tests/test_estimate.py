"""`estimate`: the pinned formula, the per-window tier cliff, and the chunked-survey pricing."""

from pathlib import Path

import pytest

from osrforge.estimate import (
    INPUT_USD_PER_TOKEN,
    LARGE_INPUT_USD_PER_TOKEN,
    LARGE_OUTPUT_USD_PER_TOKEN,
    OUTPUT_USD_PER_TOKEN,
    CostEstimate,
    _estimate_from_measurements,
    estimate,
)
from osrforge.settings import ConversionSettings
from osrforge.workdir import Workdir

MINIMOD = Path(__file__).parent / "assets" / "minimod"


def test_minimod_estimate_is_pinned(tmp_path: Path):
    result = estimate(MINIMOD / "minimod.pdf", tmp_path / "mod.forge")
    assert result.page_count == 5
    assert result.text_tokens == 462
    assert result.image_tokens == 5 * 905
    assert result.survey_window_count == 1
    assert result.survey_input_tokens == 462 + 4525 + 2000
    assert result.survey_output_tokens == 5 * 70
    assert result.content_input_tokens == 6234  # ceil(1.25 * (462 + 4525))
    assert result.content_output_tokens == 5 * 550
    assert result.monsters_input_tokens == 5000
    assert result.monsters_output_tokens == 500
    assert result.input_tokens == 6987 + 6234 + 5000
    assert result.output_tokens == 350 + 2750 + 500
    expected_usd = result.input_tokens * INPUT_USD_PER_TOKEN + result.output_tokens * OUTPUT_USD_PER_TOKEN
    assert result.usd == pytest.approx(expected_usd)


def test_estimate_leaves_a_warm_workdir(tmp_path: Path):
    root = tmp_path / "mod.forge"
    estimate(MINIMOD / "minimod.pdf", root)
    workdir = Workdir(root)
    assert workdir.run_json.is_file()
    assert workdir.page_png(1).is_file() and workdir.page_txt(1).is_file()
    # No model artifacts: preprocess only.
    assert not workdir.stages_dir.exists()
    assert not workdir.adventure_json.exists()


def test_a_single_request_survey_crosses_the_tier_cliff_alone():
    settings = ConversionSettings(survey_max_pages=100)
    # 100 pages of very dense text: survey input 392,500 tokens — over 272K.
    result = _estimate_from_measurements([3_000] * 100, settings=settings)
    assert result.survey_window_count == 1
    assert result.survey_input_tokens > 272_000
    expected_usd = (
        result.survey_input_tokens * LARGE_INPUT_USD_PER_TOKEN
        + result.survey_output_tokens * LARGE_OUTPUT_USD_PER_TOKEN
        + (result.content_input_tokens + result.monsters_input_tokens) * INPUT_USD_PER_TOKEN
        + (result.content_output_tokens + result.monsters_output_tokens) * OUTPUT_USD_PER_TOKEN
    )
    assert result.usd == pytest.approx(expected_usd)


def test_below_the_cliff_everything_prices_at_the_base_tier():
    result = _estimate_from_measurements([100] * 10, settings=ConversionSettings())
    expected_usd = result.input_tokens * INPUT_USD_PER_TOKEN + result.output_tokens * OUTPUT_USD_PER_TOKEN
    assert result.usd == pytest.approx(expected_usd)


def test_an_over_chunk_size_source_prices_per_window_at_pinned_numbers():
    # 200 pages at a 150-page chunk size: windows 1-150 and 151-200, each
    # carrying its own pages' tokens plus the flat 2,000 overhead.
    result = _estimate_from_measurements([10] * 200, settings=ConversionSettings(survey_max_pages=150))
    assert result.survey_window_count == 2
    first_window_input = 150 * 10 + 150 * 905 + 2000  # 139,250
    second_window_input = 50 * 10 + 50 * 905 + 2000  # 47,750
    assert result.survey_input_tokens == first_window_input + second_window_input == 187_000
    assert result.survey_output_tokens == 150 * 70 + 50 * 70 == 14_000
    # Neither window crosses 272K: everything at the base tier.
    expected_usd = result.input_tokens * INPUT_USD_PER_TOKEN + result.output_tokens * OUTPUT_USD_PER_TOKEN
    assert result.usd == pytest.approx(expected_usd)


def test_the_tier_cliff_applies_per_window_for_text_dense_sources():
    # The B3 calibration density (~1,015 text tokens/page) puts a 150-page
    # window at 290,000 input tokens — over the cliff, chunk size or not.
    result = _estimate_from_measurements([1_015] * 300, settings=ConversionSettings(survey_max_pages=150))
    assert result.survey_window_count == 2
    window_input = 150 * 1_015 + 150 * 905 + 2000
    assert window_input > 272_000
    assert result.survey_input_tokens == 2 * window_input
    survey_usd = 2 * (window_input * LARGE_INPUT_USD_PER_TOKEN + 150 * 70 * LARGE_OUTPUT_USD_PER_TOKEN)
    expected_usd = (
        survey_usd
        + (result.content_input_tokens + result.monsters_input_tokens) * INPUT_USD_PER_TOKEN
        + (result.content_output_tokens + result.monsters_output_tokens) * OUTPUT_USD_PER_TOKEN
    )
    assert result.usd == pytest.approx(expected_usd)


def test_the_tier_cliff_still_fires_for_a_single_request_survey_with_the_knob_raised():
    result = _estimate_from_measurements([1_015] * 300, settings=ConversionSettings(survey_max_pages=400))
    assert result.survey_window_count == 1
    assert result.survey_input_tokens == 300 * 1_015 + 300 * 905 + 2000 > 272_000
    expected_survey_usd = (
        result.survey_input_tokens * LARGE_INPUT_USD_PER_TOKEN
        + result.survey_output_tokens * LARGE_OUTPUT_USD_PER_TOKEN
    )
    expected_usd = (
        expected_survey_usd
        + (result.content_input_tokens + result.monsters_input_tokens) * INPUT_USD_PER_TOKEN
        + (result.content_output_tokens + result.monsters_output_tokens) * OUTPUT_USD_PER_TOKEN
    )
    assert result.usd == pytest.approx(expected_usd)


def test_exceeds_survey_guard_is_gone_from_the_result_type():
    # The guard failure it reported no longer exists — larger sources chunk.
    assert "exceeds_survey_guard" not in {field for field in CostEstimate.__dataclass_fields__}


def test_the_extraction_runner_imports_the_pricing_constants():
    runner = (Path(__file__).parent.parent / "tools" / "extract" / "run_extraction.py").read_text(encoding="utf-8")
    assert "from osrforge.estimate import INPUT_USD_PER_TOKEN, OUTPUT_USD_PER_TOKEN" in runner
    assert "2.50" not in runner  # no second copy of the price


def test_the_default_chunk_size_is_the_measured_image_cap():
    # The deployment rejects requests with more than 50 images (measured live
    # on a 54-page module), so the default keeps every window under the cap.
    assert ConversionSettings().survey_max_pages == 50
    result = _estimate_from_measurements([10] * 54, settings=ConversionSettings())
    assert result.survey_window_count == 2
