"""`estimate`: the pinned formula, the tier cliff, and the survey guard."""

from pathlib import Path

import pytest

from osrforge.estimate import (
    INPUT_USD_PER_TOKEN,
    LARGE_INPUT_USD_PER_TOKEN,
    LARGE_OUTPUT_USD_PER_TOKEN,
    OUTPUT_USD_PER_TOKEN,
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
    assert result.exceeds_survey_guard is False


def test_estimate_leaves_a_warm_workdir(tmp_path: Path):
    root = tmp_path / "mod.forge"
    estimate(MINIMOD / "minimod.pdf", root)
    workdir = Workdir(root)
    assert workdir.run_json.is_file()
    assert workdir.page_png(1).is_file() and workdir.page_txt(1).is_file()
    # No model artifacts: preprocess only.
    assert not workdir.stages_dir.exists()
    assert not workdir.adventure_json.exists()


def test_the_survey_request_crosses_the_tier_cliff_alone():
    settings = ConversionSettings()
    # 100 pages of very dense text: survey input 392,500 tokens — over 272K.
    result = _estimate_from_measurements(page_count=100, text_tokens=300_000, settings=settings)
    assert result.survey_input_tokens > 272_000
    expected_usd = (
        result.survey_input_tokens * LARGE_INPUT_USD_PER_TOKEN
        + result.survey_output_tokens * LARGE_OUTPUT_USD_PER_TOKEN
        + (result.content_input_tokens + result.monsters_input_tokens) * INPUT_USD_PER_TOKEN
        + (result.content_output_tokens + result.monsters_output_tokens) * OUTPUT_USD_PER_TOKEN
    )
    assert result.usd == pytest.approx(expected_usd)


def test_below_the_cliff_everything_prices_at_the_base_tier():
    result = _estimate_from_measurements(page_count=10, text_tokens=1_000, settings=ConversionSettings())
    expected_usd = result.input_tokens * INPUT_USD_PER_TOKEN + result.output_tokens * OUTPUT_USD_PER_TOKEN
    assert result.usd == pytest.approx(expected_usd)


def test_exceeds_survey_guard_reports_the_run_that_cannot_happen():
    settings = ConversionSettings()
    over = _estimate_from_measurements(page_count=settings.survey_max_pages + 1, text_tokens=0, settings=settings)
    assert over.exceeds_survey_guard is True
    at_limit = _estimate_from_measurements(page_count=settings.survey_max_pages, text_tokens=0, settings=settings)
    assert at_limit.exceeds_survey_guard is False


def test_the_extraction_runner_imports_the_pricing_constants():
    runner = (Path(__file__).parent.parent / "tools" / "extract" / "run_extraction.py").read_text(encoding="utf-8")
    assert "from osrforge.estimate import INPUT_USD_PER_TOKEN, OUTPUT_USD_PER_TOKEN" in runner
    assert "2.50" not in runner  # no second copy of the price
