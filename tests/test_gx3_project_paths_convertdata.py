from pathlib import Path

from gx3cli.gx3_project_paths import convertdata_path, iter_convertdata_entries, is_extracted_gx3_root


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
