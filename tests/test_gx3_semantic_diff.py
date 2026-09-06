from __future__ import annotations

import csv
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_ladder_logic import enable_logic_for_device, logic_to_text
from gx3cli.gx3_semantic_diff import compare_configuration, logic_signature
from gx3cli.review_gx3_project import LadderRow


def branch_rung() -> str:
    data, _, _ = generate_rung(
        {"or": [{"device": "M100"}, {"device": "M101"}]},
        {"type": "coil", "device": "M200"},
    )
    return data


def test_disconnected_branch_changes_logic() -> None:
    old = branch_rung()
    new = old.replace("v{pos=1,1}", "")
    formulas = []
    for data in (old, new):
        row = LadderRow("test", 0, "", "", 0, 2, data, "", [], "exact")
        formulas.append(logic_to_text(enable_logic_for_device(row, "M200")))
    assert formulas[0] != formulas[1], formulas
    assert logic_signature(old) != logic_signature(new)


def test_edge_contact_is_not_layout_only() -> None:
    old = branch_rung()
    assert logic_signature(old) != logic_signature(old.replace("ct=a", "ct=p", 1))


def test_unknown_operand_changes_are_preserved() -> None:
    old = branch_rung().replace("d{s=#:a=100:vt=nn}", "unresolved{value=one}", 1)
    new = old.replace("value=one", "value=two")
    assert logic_signature(old) != logic_signature(new)


def test_canvas_size_only_is_layout_only() -> None:
    old = branch_rung()
    assert logic_signature(old) == logic_signature(old.replace("dim=2x2", "dim=20x20", 1))


def test_element_movement_is_conservatively_reported() -> None:
    old = branch_rung()
    new = old.replace("pos=0,1", "pos=1,1", 1)
    assert logic_signature(old) != logic_signature(new)


def make_module(path: Path, settings: list[tuple[str, str, str]]) -> None:
    schema = """
    create table {name} (
        Label text, DataArrayIndexX text, DataArrayIndexY text,
        Data text, DataDefault text, ParamGroup text
    );
    """
    with closing(sqlite3.connect(path)) as con:
        for name in ("DeviceInfo", "ProfileTableInfo", "PARAM_BasicSetting"):
            con.executescript(schema.format(name=name))
        con.executemany(
            "insert into DeviceInfo(Label, DataArrayIndexX, Data) values (?, ?, ?)",
            [
                ("DeviceModel", "1", "RD81RC96"),
                ("_HeadIO", "1", "2368"),
                ("_BaseNo", "1", "1"),
                ("_SlotNo", "1", "3"),
            ],
        )
        con.executemany(
            "insert into ProfileTableInfo(Label, Data) values (?, ?)",
            [("DeviceInfo", "DEVICEINFO"), ("PARAM_BasicSetting", "CARDINFO")],
        )
        con.executemany(
            "insert into PARAM_BasicSetting(Label, DataArrayIndexX, Data, DataDefault) values (?, ?, ?, '')",
            settings,
        )
        con.commit()


def make_dm(path: Path, value: int) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.execute(
            "create table MEMORY_DATA("
            "MemorySEQ integer, DevCode integer, ExtCode integer, ExtNo integer, "
            "DevNo integer, MemSize integer, MemData blob)"
        )
        con.execute(
            "insert into MEMORY_DATA values (?, ?, ?, ?, ?, ?, ?)",
            (1, 32, 0, 0, 100, 1, struct.pack("<H", value)),
        )
        con.commit()


def section(results: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(result for result in results if result["name"] == name)


def test_module_parameter_input_order_is_normalized() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_config_order_") as tmp:
        root = Path(tmp)
        old, new = root / "old", root / "new"
        old.mkdir()
        new.mkdir()
        values = [("BasePrm3", "1", "192.0.2.10"), ("BasePrm6", "1", "800")]
        make_module(old / "3010658289117734017.db", values)
        make_module(new / "3010658289117734017.db", list(reversed(values)))
        results, detail = compare_configuration(old, new)
        assert section(results, "module-params")["state"] == "same", results
        assert not [row for row in detail if row["pou"] == "module-params"], detail


def test_one_module_parameter_change_reports_only_that_setting() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_config_module_") as tmp:
        root = Path(tmp)
        old, new = root / "old", root / "new"
        old.mkdir()
        new.mkdir()
        make_module(
            old / "3010658289117734017.db",
            [("BasePrm3", "1", "192.0.2.10"), ("BasePrm6", "1", "800")],
        )
        make_module(
            new / "3010658289117734017.db",
            [("BasePrm3", "1", "192.0.2.10"), ("BasePrm6", "1", "801")],
        )
        results, detail = compare_configuration(old, new)
        result = section(results, "module-params")
        changed = [row for row in detail if row["pou"] == "module-params"]
        assert result["state"] == "changed" and result["changes"] == 1, results
        assert len(changed) == 1, changed
        assert "BasePrm6" in str(changed[0]["title"]), changed
        assert "800" in str(changed[0]["summary"]) and "801" in str(changed[0]["summary"]), changed


def test_one_device_memory_value_change_reports_the_device() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_config_dm_") as tmp:
        root = Path(tmp)
        old, new = root / "old", root / "new"
        old.mkdir()
        new.mkdir()
        make_dm(old / "001_DM.db", 5)
        make_dm(new / "001_DM.db", 6)
        results, detail = compare_configuration(old, new)
        result = section(results, "device-memory")
        changed = [row for row in detail if row["pou"] == "device-memory"]
        assert result["state"] == "changed" and result["changes"] == 1, results
        assert len(changed) == 1, changed
        assert "D100" in str(changed[0]["title"]), changed
        assert "5" in str(changed[0]["summary"]) and "6" in str(changed[0]["summary"]), changed


def test_missing_and_unreadable_are_not_same() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_config_states_") as tmp:
        root = Path(tmp)
        old, new = root / "old", root / "new"
        old.mkdir()
        new.mkdir()
        # old: no module database -> missing. new: a numeric database exists but is unreadable.
        (new / "3010658289117734017.db").write_bytes(b"not a sqlite database")
        results, detail = compare_configuration(old, new)
        result = section(results, "module-params")
        assert result["old_state"] == "missing", result
        assert result["new_state"] == "unreadable", result
        assert result["state"] == "unreadable", result
        changed = [row for row in detail if row["pou"] == "module-params"]
        assert len(changed) == 1 and changed[0]["kind"] == "config-unreadable", changed


def test_cli_reports_changes_in_default_output() -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gx3_diff_test_") as tmp:
        root = Path(tmp)
        old = branch_rung()
        variants = [
            old.replace("v{pos=1,1}", ""),
            old.replace("ct=a", "ct=p", 1),
            old.replace("dim=2x2", "dim=20x20", 1),
            old.replace("s=ce{", "s=uninterpreted{", 1),
        ]
        for name, data_rows in (("old", [old] * 4), ("new", variants)):
            folder = root / name
            folder.mkdir()
            with closing(sqlite3.connect(folder / "001_LDDB.db")) as con:
                con.execute("create table LadderBlocks(id text, pos real, blocktype integer, data text)")
                con.executemany(
                    "insert into LadderBlocks values (?, ?, 0, ?)",
                    [(f"_guid/synthetic-{i}", i, data) for i, data in enumerate(data_rows)],
                )
                con.commit()
        output = root / "diff.csv"
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_cli", "semantic-diff",
             str(root / "old"), str(root / "new"), "-o", str(output)],
            cwd=root, env=env, text=True, encoding="utf-8", capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "logic-changed=3 layout-only=1" in result.stdout, result.stdout
        assert "old=exact, new=partial" in result.stdout, result.stdout
        assert "project-config.cpu" in result.stdout and "missing" in result.stdout, result.stdout
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        # Both projects omit the same configuration sources, so unavailable
        # state is surfaced in stdout but is not itself a semantic difference.
        assert [row["kind"] for row in rows] == ["logic", "logic", "logic"], rows
        assert "old=exact, new=partial" in rows[-1]["summary"], rows
        assert all("argument order changed" != row["summary"] for row in rows), rows


def test_cli_reports_config_change_without_ladder_change() -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gx3_config_only_") as tmp:
        root = Path(tmp)
        old, new = root / "old", root / "new"
        old.mkdir()
        new.mkdir()
        make_module(old / "3010658289117734017.db", [("BasePrm6", "1", "800")])
        make_module(new / "3010658289117734017.db", [("BasePrm6", "1", "801")])
        output = root / "diff.csv"
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_cli", "semantic-diff",
             str(old), str(new), "-o", str(output), "--skip-comments"],
            cwd=root, env=env, text=True, encoding="utf-8", capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "logic-changed=0" in result.stdout, result.stdout
        assert "module-params" in result.stdout and "changed" in result.stdout, result.stdout
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert len(rows) == 1 and rows[0]["kind"] == "config-changed", rows
        assert "BasePrm6" in rows[0]["title"], rows


def test_summary_decodes_each_side_once() -> None:
    from gx3cli import gx3_semantic_diff as diff

    old = branch_rung()
    with patch.object(diff, "parse_row_operations", wraps=diff.parse_row_operations) as decode:
        summary = diff.summarize_change(old, old.replace("a=100", "a=102", 1))
    assert decode.call_count == 2, decode.call_count
    assert "M102" in summary, summary


def main() -> int:
    test_disconnected_branch_changes_logic()
    test_edge_contact_is_not_layout_only()
    test_unknown_operand_changes_are_preserved()
    test_canvas_size_only_is_layout_only()
    test_element_movement_is_conservatively_reported()
    test_module_parameter_input_order_is_normalized()
    test_one_module_parameter_change_reports_only_that_setting()
    test_one_device_memory_value_change_reports_the_device()
    test_missing_and_unreadable_are_not_same()
    test_cli_reports_changes_in_default_output()
    test_cli_reports_config_change_without_ladder_change()
    test_summary_decodes_each_side_once()
    print("12 semantic-diff regression checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
