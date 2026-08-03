from __future__ import annotations

"""Version helpers for console entry points and diagnostics."""

from importlib import metadata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore[assignment]


PROJECT_NAME = "gx3-cli-mcp"


def package_version() -> str:
    """Return the package version with pyproject.toml as the source fallback."""

    try:
        return metadata.version(PROJECT_NAME)
    except metadata.PackageNotFoundError:
        pass

    return _version_from_pyproject(Path(__file__).resolve().parents[1] / "pyproject.toml")


def _version_from_pyproject(pyproject: Path) -> str:
    """Read the project version from pyproject.toml.

    Python 3.11+ has tomllib. Python 3.10 does not, so keep a tiny fallback for
    the simple PEP 621 `version = "..."` field used by this project.
    """

    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "0+unknown"

    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            version = data.get("project", {}).get("version")
        except tomllib.TOMLDecodeError:
            version = None
        return str(version or "0+unknown")

    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "[project]":
            in_project = True
            continue
        if in_project and line.startswith("["):
            break
        if in_project and line.startswith("version"):
            _, _, value = line.partition("=")
            return value.strip().strip('"') or "0+unknown"
    return "0+unknown"


def version_line(entrypoint: str) -> str:
    return f"{entrypoint} {package_version()}"
