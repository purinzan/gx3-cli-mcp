from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.release_gate import expand_artifact_args


ROOT = Path(__file__).resolve().parents[1]


def test_all_markdown_files_are_linked_from_readme() -> None:
    # Compare on POSIX separators so the check behaves the same whether the
    # tests run on Windows or on a POSIX host.
    readme = (ROOT / "README.md").read_text(encoding="utf-8").replace("\\", "/")
    missing: list[str] = []
    for path in ROOT.rglob("*.md"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "README.md":
            continue
        if rel not in readme:
            missing.append(rel)
    assert not missing, "README.md must link every markdown file: " + ", ".join(sorted(missing))


def test_file_usage_guide_indexes_repository_files() -> None:
    guide = (ROOT / "docs" / "FILE_USAGE_GUIDE_JA.md").read_text(encoding="utf-8")
    missing: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".git", "__pycache__", ".pytest_cache", "build", "dist"} for part in path.parts):
            continue
        if any(part.endswith(".egg-info") for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel == "docs/FILE_USAGE_GUIDE_JA.md":
            continue
        if path.name not in guide:
            missing.append(rel)
    assert not missing, "FILE_USAGE_GUIDE_JA.md must index every file: " + ", ".join(sorted(missing))


def test_release_gate_expands_shell_wildcards() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wheel = Path(tmp) / "gx3_cli_mcp-0.1.0-py3-none-any.whl"
        wheel.write_bytes(b"")
        assert expand_artifact_args([str(Path(tmp) / "gx3_cli_mcp-*.whl")]) == [str(wheel)]


def main() -> int:
    test_all_markdown_files_are_linked_from_readme()
    test_file_usage_guide_indexes_repository_files()
    test_release_gate_expands_shell_wildcards()
    print("documentation navigation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
