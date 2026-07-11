"""Prove the built wheel installs and works in a clean environment.

Run this with a fresh venv's interpreter after installing the wheel by path
into that venv — never against the repository checkout, and it imports nothing
from `tests/` or `tools/`. It asserts the import resolves under site-packages
rather than a shadowing checkout, that installed metadata matches the expected
version, that the façade exports exactly the spec's library API, that the
`osrforge` console script prints the version, and that the pinned osrlib
dependency resolved.

Usage:
    <fresh-venv-python> tools/release/install_smoke.py EXPECTED_VERSION
"""

import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path


def check_resolution(expected_version: str) -> None:
    """Assert osrforge resolves from site-packages at the expected version."""
    import osrforge

    location = Path(osrforge.__file__).resolve()
    assert "site-packages" in location.parts, f"osrforge resolved outside site-packages: {location}"
    installed = importlib.metadata.version("osr-forge")
    assert installed == expected_version, f"installed metadata reports {installed}, expected {expected_version}"
    print(f"ok: osrforge {installed} imported from {location.parent}")


def check_facade() -> None:
    """Assert the façade exports exactly the spec's library API."""
    import osrforge

    expected = ["ConversionSettings", "assemble", "check", "convert", "estimate"]
    assert sorted(osrforge.__all__) == expected, f"facade exports {sorted(osrforge.__all__)}, expected {expected}"
    for name in expected:
        assert getattr(osrforge, name, None) is not None, f"facade export {name} is missing"
    print("ok: the facade exports exactly the library API")


def check_console_script(expected_version: str) -> None:
    """Assert the console script runs and prints the version."""
    script = Path(sys.executable).parent / "osrforge"
    result = subprocess.run([str(script), "--version"], capture_output=True, text=True, check=True)
    output = result.stdout.strip()
    assert output == f"osrforge {expected_version}", f"--version printed {output!r}"
    print(f"ok: {output}")


def check_osrlib_resolves() -> None:
    """Assert the pinned osrlib dependency installed and its catalog loads."""
    from osrlib.data import load_monsters

    catalog = load_monsters()
    assert catalog.monsters, "the osrlib monster catalog loaded empty"
    print(f"ok: osrlib {importlib.metadata.version('osrlib')} resolved with {len(catalog.monsters)} monsters")


def main() -> int:
    """Run every smoke check in order.

    Returns:
        0 when the installed wheel passes; assertions abort otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the expected package version, e.g. 0.1.0")
    args = parser.parse_args()

    check_resolution(args.version)
    check_facade()
    check_console_script(args.version)
    check_osrlib_resolves()
    print(f"install smoke passed: osr-forge {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
