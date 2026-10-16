"""The tracing stage's two provider roles, and how every other stage keeps its own provider."""

from pathlib import Path

from osrforge.providers.base import ModelProvider
from osrforge.providers.decisions import DecisionProvider, DecisionRequest, DecisionResponse
from osrforge.providers.fixtures import FixtureProvider
from osrforge.providers.reading import ReadingProvider, ReadingRequest, ReadingResponse
from osrforge.tracing.stage import TracingProviders


class StubReading:
    def read(self, request: ReadingRequest) -> ReadingResponse:
        raise AssertionError("never called")

    def read_all(self, requests) -> list[ReadingResponse | Exception]:
        raise AssertionError("never called")


class StubDecisions:
    def decide(self, request: DecisionRequest) -> DecisionResponse:
        raise AssertionError("never called")


def test_the_three_roles_are_distinct_protocols(tmp_path: Path):
    reading, decisions, extraction = StubReading(), StubDecisions(), FixtureProvider(tmp_path)
    assert isinstance(reading, ReadingProvider) and not isinstance(reading, ModelProvider)
    assert isinstance(decisions, DecisionProvider) and not isinstance(decisions, ModelProvider)
    assert isinstance(extraction, ModelProvider)
    assert not isinstance(extraction, ReadingProvider) and not isinstance(extraction, DecisionProvider)
    assert not isinstance(reading, DecisionProvider) and not isinstance(decisions, ReadingProvider)


def test_the_tracing_stage_takes_its_two_providers_as_a_pair():
    providers = TracingProviders(reading=StubReading(), decisions=StubDecisions())
    assert isinstance(providers.reading, ReadingProvider)
    assert isinstance(providers.decisions, DecisionProvider)
