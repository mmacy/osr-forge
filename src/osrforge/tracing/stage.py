"""The tracing stage's entry point and its two provider roles.

The tracing stage reads each printed map with two model services, each for
the job it measured best at: a reading provider (Claude) reads each map page
whole and judges the items the decision provider is unsure of, and a decision
provider (Clef) answers the bulk questions about each cell, edge, and key.
Every other stage stays on its [`ModelProvider`][osrforge.providers.base.ModelProvider].
The stage itself arrives with the chunk that builds it.
"""

from dataclasses import dataclass

from osrforge.providers.decisions import DecisionProvider
from osrforge.providers.reading import ReadingProvider
from osrforge.workdir import Workdir

__all__ = ["TracingProviders", "trace"]


@dataclass(frozen=True)
class TracingProviders:
    """The two providers the tracing stage takes.

    Attributes:
        reading: The provider that reads each page whole and judges uncertain
            items. Claude fills it.
        decisions: The provider that answers one question about one cell,
            edge, or key. Clef fills it.
    """

    reading: ReadingProvider
    decisions: DecisionProvider


def trace(workdir: Workdir, providers: TracingProviders) -> None:
    """Run the tracing stage over a workdir's map pages.

    Args:
        workdir: The workdir, with the survey, content, and map-reading
            caches complete.
        providers: The reading and decision providers.
    """
    raise NotImplementedError("chunk: tracing-stage")
