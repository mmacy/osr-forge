"""Audit the built wheel and sdist before either can reach PyPI.

Stdlib-only, run against artifacts rather than the repository tree — which is
why it is a script here and not a pytest module. Given a dist directory and
the expected version, it asserts the wheel is pure (`py3-none-any`), carries
the complete `osrforge/` module tree from the checked-in `src/osrforge/`
listing (with `py.typed`) and its dist-info only, ships the license text, and
stamps the expected metadata; that the sdist carries the build inputs; and —
the licensing fence, machine-checked — that neither artifact contains any path
under `tests/` or `tools/`, any `.pdf`, `.png`, or fixture JSON, or bytecode.

Usage:
    python tools/release/check_dist.py DIST_DIR EXPECTED_VERSION
"""

import argparse
import email
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PACKAGE = REPO_ROOT / "src" / "osrforge"

FORBIDDEN_DIRS = {"tests", "tools", "docs", "site", "__pycache__"}
FORBIDDEN_SUFFIXES = (".pyc", ".pdf", ".png")


def expected_package_files() -> set[str]:
    """Return the checked-in package tree as wheel-relative member names.

    Returns:
        Every file under `src/osrforge/` (bytecode caches excluded) as an
        `osrforge/...` POSIX path — the exact set the wheel must contain.
    """
    return {
        f"osrforge/{path.relative_to(SRC_PACKAGE).as_posix()}"
        for path in SRC_PACKAGE.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def forbidden_members(members: list[str], skip_leading: int) -> list[str]:
    """Return the members that leak repository directories, game content, or bytecode.

    Args:
        members: Archive member names, POSIX-style.
        skip_leading: Path components to ignore before checking — 1 for an
            sdist (its versioned root directory), 0 for a wheel.

    Returns:
        The offending member names, empty when the artifact is clean.
    """
    offenders = []
    for member in members:
        parts = Path(member).parts[skip_leading:]
        if any(part in FORBIDDEN_DIRS for part in parts) or member.endswith(FORBIDDEN_SUFFIXES):
            offenders.append(member)
        elif member.endswith(".json") and "fixture" in member.lower():
            offenders.append(member)
    return offenders


def check_wheel(wheel_path: Path, version: str, project: dict, errors: list[str]) -> None:
    """Audit the wheel's tag, contents, license, and METADATA.

    Args:
        wheel_path: The built `.whl` file.
        version: The expected package version.
        project: The `[project]` table from the repository's `pyproject.toml`.
        errors: The running failure list; findings are appended, not raised.
    """
    expected_name = f"osr_forge-{version}-py3-none-any.whl"
    if wheel_path.name != expected_name:
        errors.append(f"wheel is named {wheel_path.name}, expected {expected_name}")

    with zipfile.ZipFile(wheel_path) as wheel:
        members = [name for name in wheel.namelist() if not name.endswith("/")]
        dist_info = f"osr_forge-{version}.dist-info"

        missing = sorted(expected_package_files() - set(members))
        if missing:
            errors.append(f"wheel is missing package files: {missing}")

        # Named must-have, asserted independently of the tree listing:
        # deleting it from src/ must fail the audit, not shrink expectations.
        if "osrforge/py.typed" not in members:
            errors.append("wheel is missing osrforge/py.typed")

        # The wheel carries osrforge/** and its dist-info only — no other roots.
        strays = sorted(
            member for member in members if not member.startswith(("osrforge/", f"{dist_info}/"))
        )
        if strays:
            errors.append(f"wheel carries files outside osrforge/ and its dist-info: {strays}")

        if f"{dist_info}/licenses/LICENSE" not in members:
            errors.append(f"wheel is missing {dist_info}/licenses/LICENSE")

        try:
            wheel_file = wheel.read(f"{dist_info}/WHEEL").decode("utf-8")
        except KeyError:
            errors.append(f"wheel is missing {dist_info}/WHEEL")
        else:
            tags = [line.split(":", 1)[1].strip() for line in wheel_file.splitlines() if line.startswith("Tag:")]
            if tags != ["py3-none-any"]:
                errors.append(f"wheel tags are {tags}, expected ['py3-none-any']")

        try:
            metadata = email.message_from_bytes(wheel.read(f"{dist_info}/METADATA"))
        except KeyError:
            errors.append(f"wheel is missing {dist_info}/METADATA")
        else:
            for field, expected in (
                ("Name", project["name"]),
                ("Version", version),
                ("License-Expression", project["license"]),
                ("Requires-Python", project["requires-python"]),
            ):
                actual = metadata.get(field)
                if actual != expected:
                    errors.append(f"wheel METADATA {field} is {actual!r}, expected {expected!r}")
            license_classifiers = [
                value for value in metadata.get_all("Classifier", []) if value.startswith("License ::")
            ]
            if license_classifiers:
                errors.append(
                    f"wheel METADATA carries License classifiers beside the expression: {license_classifiers}"
                )

        offenders = forbidden_members(members, skip_leading=0)
        if offenders:
            errors.append(f"wheel leaks repository files: {offenders}")


def check_sdist(sdist_path: Path, version: str, errors: list[str]) -> None:
    """Audit the sdist's build inputs and cleanliness.

    Args:
        sdist_path: The built `.tar.gz` file.
        version: The expected package version.
        errors: The running failure list; findings are appended, not raised.
    """
    expected_name = f"osr_forge-{version}.tar.gz"
    if sdist_path.name != expected_name:
        errors.append(f"sdist is named {sdist_path.name}, expected {expected_name}")

    with tarfile.open(sdist_path, "r:gz") as sdist:
        members = [member.name for member in sdist.getmembers() if member.isfile()]

    root = f"osr_forge-{version}"
    for required in ("pyproject.toml", "README.md", "LICENSE"):
        if f"{root}/{required}" not in members:
            errors.append(f"sdist is missing {root}/{required}")

    expected_tree = {f"{root}/src/{name}" for name in expected_package_files()}
    missing = sorted(expected_tree - set(members))
    if missing:
        errors.append(f"sdist is missing source files: {missing}")

    offenders = forbidden_members(members, skip_leading=1)
    if offenders:
        errors.append(f"sdist leaks repository files: {offenders}")


def main() -> int:
    """Run the audit and report every failure at once.

    Returns:
        0 when both artifacts pass, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist_dir", type=Path, help="directory holding the built wheel and sdist")
    parser.add_argument("version", help="the expected package version, e.g. 0.1.0")
    args = parser.parse_args()

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]

    errors: list[str] = []
    wheels = sorted(args.dist_dir.glob("*.whl"))
    sdists = sorted(args.dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1:
        errors.append(f"expected exactly one wheel in {args.dist_dir}, found {[p.name for p in wheels]}")
    if len(sdists) != 1:
        errors.append(f"expected exactly one sdist in {args.dist_dir}, found {[p.name for p in sdists]}")

    if len(wheels) == 1:
        check_wheel(wheels[0], args.version, project, errors)
    if len(sdists) == 1:
        check_sdist(sdists[0], args.version, errors)

    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"dist audit passed: {args.dist_dir} holds a clean osr-forge {args.version} wheel and sdist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
