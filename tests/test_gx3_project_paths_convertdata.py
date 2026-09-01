from pathlib import Path

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
    target = tmp_path / "ConvertData\\123\\PouPCode.pcode"
    target.write_bytes(b"pcode")

    assert pcode_path_for_stepinfo(tmp_path, "123_StepInfo.db") == target
