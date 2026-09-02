from __future__ import annotations

"""Project layout probes, over a throwaway directory.

These used to take pytest's tmp_path fixture. run_tests.py runs each test file
as a plain script and pytest is not a dependency, so the file had no runner and
was skipped in silence -- it passed CI without executing once. It makes its own
temporary directory now, like the rest of the suite.
"""

import os
import tempfile
from pathlib import Path

# A .gx3 archive records its entries with backslashes, because GX Works3 writes
# it on Windows. Extracted on Windows those become real directories; extracted
# on POSIX with some tools they stay in the name, and the project arrives as
# single files called "ConvertData\\123\\Program.qpg". That layout is what the
# two tests below are about, and it cannot occur on Windows -- there the same
# archive produces the normal layout, which the other tests already cover.
BACKSLASH_LAYOUT_POSSIBLE = os.sep != "\\"

from gx3cli.gx3_intermediate_tool import pcode_path_for_stepinfo
from gx3cli.gx3_project_paths import (
    archive_container_kind,
    convertdata_path,
    iter_convertdata_entries,
    is_extracted_gx3_root,
)


def test_archive_container_kind_detects_zip_and_7z(tmp_path: Path) -> None:
    zip_file = tmp_path / "zip.gx3"
    zip_file.write_bytes(b"PK\x03\x04rest")
    seven_zip_file = tmp_path / "seven.gx3"
    seven_zip_file.write_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04rest")
    unknown_file = tmp_path / "plain.gx3"
    unknown_file.write_text("not an archive", encoding="utf-8")

    assert archive_container_kind(zip_file) == "zip"
    assert archive_container_kind(seven_zip_file) == "7z"
    assert archive_container_kind(unknown_file) == "unknown"


def test_iter_convertdata_entries_supports_normal_layout(tmp_path: Path) -> None:
    program = tmp_path / "ConvertData" / "123"
    program.mkdir(parents=True)
    target = program / "Program.qpg"
    target.write_bytes(b"qpg")

    entries = iter_convertdata_entries(tmp_path, "Program.qpg")

    assert [(entry.program_id, entry.member_name, entry.path) for entry in entries] == [
        ("123", "Program.qpg", target)
    ]
    assert convertdata_path(tmp_path, "123", "Program.qpg") == target


def test_iter_convertdata_entries_supports_backslash_preserved_layout(tmp_path: Path) -> None:
    if not BACKSLASH_LAYOUT_POSSIBLE:
        return
    target = tmp_path / "ConvertData\\123\\Program.qpg"
    target.write_bytes(b"qpg")

    entries = iter_convertdata_entries(tmp_path, "Program.qpg")

    assert [(entry.program_id, entry.member_name, entry.path) for entry in entries] == [
        ("123", "Program.qpg", target)
    ]
    assert convertdata_path(tmp_path, "123", "Program.qpg") == target


def test_fbddb_project_counts_as_extracted_gx3_root(tmp_path: Path) -> None:
    (tmp_path / "abc_FBDDB.db").write_bytes(b"")

    assert is_extracted_gx3_root(tmp_path)


def test_pcode_path_for_stepinfo_supports_backslash_preserved_layout(tmp_path: Path) -> None:
    if not BACKSLASH_LAYOUT_POSSIBLE:
        return
    target = tmp_path / "ConvertData\\123\\PouPCode.pcode"
    target.write_bytes(b"pcode")

    assert pcode_path_for_stepinfo(tmp_path, "123_StepInfo.db") == target


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for name, test in tests:
        with tempfile.TemporaryDirectory() as tmp:
            test(Path(tmp))
    skipped = "" if BACKSLASH_LAYOUT_POSSIBLE else " (backslash-layout cases not applicable on Windows)"
    print(f"{len(tests)} project-path checks passed{skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
