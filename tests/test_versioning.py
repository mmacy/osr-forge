from importlib import metadata

from osrforge.errors import (
    FixtureMissError,
    OsrForgeError,
    OverrideError,
    PdfError,
    ProviderError,
    SchemaValidationError,
)
from osrforge.versioning import SCHEMA_VERSION, osrforge_version


def test_schema_version_is_one():
    assert SCHEMA_VERSION == 1


def test_osrforge_version_matches_package_metadata():
    assert osrforge_version() == metadata.version("osr-forge")


def test_every_typed_exception_derives_from_osrforgeerror():
    for exception_type in (PdfError, ProviderError, SchemaValidationError, FixtureMissError, OverrideError):
        assert issubclass(exception_type, OsrForgeError)


def test_provider_error_subtypes():
    assert issubclass(SchemaValidationError, ProviderError)
    assert issubclass(FixtureMissError, ProviderError)
