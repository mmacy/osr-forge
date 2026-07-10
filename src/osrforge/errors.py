"""The osr-forge exception hierarchy.

The typed hierarchy covers runtime failures of the work itself — unreadable
sources, provider transport trouble, schema-invalid model output. Programmer
misuse raises stdlib `ValueError`/`TypeError`, never these. The hierarchy grows
additively; later phases add their own members.
"""

__all__ = [
    "ExtractionError",
    "FixtureMissError",
    "OsrForgeError",
    "OverrideError",
    "PdfError",
    "ProviderError",
    "SchemaValidationError",
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
