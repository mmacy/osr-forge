"""Artifact schema versioning for osr-forge's own artifacts.

`report.json` and `run.json` carry `schema_version` and `osrforge_version` as
top-level fields — the artifact contracts become osr-web's public API at
integration time, so they are versioned from birth and evolve additively within
a version. `adventure.json` is deliberately not covered here: osrlib's
`stamp_document` envelope (with osrlib's own `SCHEMA_VERSION`) already versions
it, and it gets no second osr-forge wrapper.
"""

from importlib import metadata

__all__ = ["SCHEMA_VERSION", "osrforge_version"]

SCHEMA_VERSION = 1
"""The current schema version for osr-forge-produced artifacts (`report.json`, `run.json`).

Additive-only within a version; renames, removals, and semantic changes bump it.
"""


def osrforge_version() -> str:
    """Return the installed osr-forge package version, for stamping into artifacts.

    Returns:
        The version string from package metadata.
    """
    return metadata.version("osr-forge")
