"""The osr-forge exception hierarchy.

The typed hierarchy covers runtime failures of the work itself — unreadable
sources, provider transport trouble, schema-invalid model output. Programmer
misuse raises stdlib `ValueError`/`TypeError`, never these. The hierarchy grows
additively; later phases add their own members.
"""

__all__ = [
    "FixtureMissError",
    "OsrForgeError",
    "PdfError",
    "ProviderError",
    "SchemaValidationError",
]


class OsrForgeError(Exception):
    """Base class for every osr-forge runtime failure."""


class PdfError(OsrForgeError):
    """The source PDF is unreadable, encrypted, or violates a configured limit."""


class ProviderError(OsrForgeError):
    """A model provider failed: transport, auth, or rate-limit exhaustion."""


class SchemaValidationError(ProviderError):
    """A model response failed the request's JSON Schema after the retry budget."""


class FixtureMissError(ProviderError):
    """No recorded fixture matches the request's fingerprint."""
