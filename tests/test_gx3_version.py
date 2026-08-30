from __future__ import annotations

import re
from pathlib import Path

import gx3cli.gx3_version as version


def _declared_version() -> str:
    """Read the version straight out of pyproject.toml, textually.

    The point of the test below is that the hand-rolled fallback parser agrees
    with the file, so comparing it against a literal would mean editing this
    test on every release -- and a test that has to be edited to stay green is
    one nobody trusts.
    """
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    return match.group(1)


def test_py310_version_fallback_without_tomllib() -> None:
    # Python 3.10 has no tomllib, so gx3_version falls back to its own parser.
    old_tomllib = version.tomllib
    version.tomllib = None
    try:
        assert version._version_from_pyproject(Path("pyproject.toml")) == _declared_version()
    finally:
        version.tomllib = old_tomllib


def test_tomllib_and_fallback_agree() -> None:
    # Whichever path a given Python takes, it must report the same version.
    if version.tomllib is None:
        return
    assert version._version_from_pyproject(Path("pyproject.toml")) == _declared_version()


def main() -> int:
    test_py310_version_fallback_without_tomllib()
    test_tomllib_and_fallback_agree()
    print("version helper checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
