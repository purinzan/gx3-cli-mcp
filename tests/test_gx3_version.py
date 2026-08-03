from __future__ import annotations

from pathlib import Path

import gx3cli.gx3_version as version


def test_py310_version_fallback_without_tomllib() -> None:
    old_tomllib = version.tomllib
    version.tomllib = None
    try:
        assert version._version_from_pyproject(Path("pyproject.toml")) == "0.1.0"
    finally:
        version.tomllib = old_tomllib


def main() -> int:
    test_py310_version_fallback_without_tomllib()
    print("version helper checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
