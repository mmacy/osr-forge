"""The osr-forge exception hierarchy.

The typed hierarchy covers runtime failures of the work itself — unreadable
sources, provider transport trouble, schema-invalid model output. Programmer
misuse raises stdlib `ValueError`/`TypeError`, never these. The hierarchy grows
additively as the package does.
"""

__all__ = [
    "ExtractionError",
    "FixtureMissError",
    "LedgerStopError",
    "OsrForgeError",
    "OverrideError",
    "PdfError",
    "ProviderError",
    "ReadingFailure",
    "SchemaValidationError",
    "StaleRecordingError",
]


class OsrForgeError(Exception):
    """Base class for every osr-forge runtime failure."""


class PdfError(OsrForgeError):
    """The source PDF is unreadable, encrypted, or violates a configured limit."""


class ExtractionError(OsrForgeError):
    """The extraction work itself failed: the survey found no dungeons or no keyed areas.

    Provider failures keep propagating as
    [`ProviderError`][osrforge.errors.ProviderError]/
    [`SchemaValidationError`][osrforge.errors.SchemaValidationError]; calling a
    stage on a workdir whose upstream stage isn't `completed` is programmer
    misuse and raises stdlib `ValueError`.
    """


class OverrideError(OsrForgeError):
    """An override entry cannot take effect.

    An unknown monster name, an unknown area or level address that isn't a
    well-formed add, contradictory entries, an edge collision after
    canonicalization, or a duplicate YAML key. The division of labor, pinned:
    addressing errors are loud (this error); content validity flows to the
    report — a dangling `template_id` takes effect and `validate_adventure`
    reports it in `report.json`.
    """


class ProviderError(OsrForgeError):
    """A model provider failed: transport, auth, or rate-limit exhaustion."""


class SchemaValidationError(ProviderError):
    """A model response failed the request's JSON Schema after the retry budget."""


class FixtureMissError(ProviderError):
    """No recorded fixture matches the request's fingerprint."""


class ReadingFailure(ProviderError):
    """A reading answer stopped at `max_tokens` or with a refusal, so there is no answer to check.

    Attributes:
        stop_reason: The stop reason the service returned: `max_tokens` or
            `refusal`.
    """

    def __init__(self, message: str, stop_reason: str) -> None:
        """Record the stop reason beside the message.

        Args:
            message: What failed, naming the request kind.
            stop_reason: The service's stop reason.
        """
        super().__init__(message)
        self.stop_reason = stop_reason


class LedgerStopError(ProviderError):
    """A request's reservation would take a ledger's spend past its stop, so the request wasn't sent."""


class StaleRecordingError(ProviderError):
    """A recording's key matches the request, but its stored body isn't the body the current code builds."""
