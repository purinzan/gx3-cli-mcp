from __future__ import annotations

"""Version helpers for console entry points and diagnostics."""

from importlib import metadata
from pathlib import Path
import tomllib


PROJECT_NAME = "gx3-cli-mcp"


def package_version() -> str:
    """Return the package version with pyproject.toml as the source fallback."""

    try:
        return metadata.version(PROJECT_NAME)
    except metadata.PackageNotFoundError:
        pass

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        version = data.get("project", {}).get("version")
    except (OSError, tomllib.TOMLDecodeError):
        version = None
    return str(version or "0+unknown")


def version_line(entrypoint: str) -> str:
    return f"{entrypoint} {package_version()}"
