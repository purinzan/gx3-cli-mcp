from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path

from gx3cli.gx3_doctor import main as doctor_main
from gx3cli.gx3_synthetic_project import create_synthetic_project


def run_doctor(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor_main(args)
    return code, buf.getvalue()


def test_missing_index_and_xref_show_next_commands() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "fixture")
        index_dir = work / "indexes"

        code, out = run_doctor(
            [
                "--root",
                str(root),
                "--index-dir",
                str(index_dir),
                "--link-db",
                str(index_dir / "link_map.sqlite"),
                "--warn-only",
                "--no-script-check",
            ]
        )

        assert code == 0
        assert "index-lite" in out
        assert f"next: gx3-cli index-lite build --root {root}" in out
        assert f"next: gx3-cli xref build --root {root}" in out
        assert "next: gx3-cli link-map build --project LABEL=<project-root>" in out


def test_missing_root_shows_path_hint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "missing.gx3"

        code, out = run_doctor(
            [
                "--root",
                str(missing),
                "--warn-only",
                "--no-script-check",
            ]
        )

        assert code == 0
        assert "missing" in out
        assert "next: check --root path" in out


def main() -> None:
    test_missing_index_and_xref_show_next_commands()
    test_missing_root_shows_path_hint()
    print("doctor next-step checks passed")


if __name__ == "__main__":
    main()
