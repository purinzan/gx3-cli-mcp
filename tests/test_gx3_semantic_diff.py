from __future__ import annotations

import csv
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_ladder_logic import enable_logic_for_device, logic_to_text
from gx3cli.gx3_semantic_diff import logic_signature
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


def test_cli_reports_changes_in_default_output() -> None:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="gx3_diff_test_") as tmp:
        root = Path(tmp)
        old = branch_rung()
        variants = [
            old.replace("v{pos=1,1}", ""),
            old.replace("ct=a", "ct=p", 1),
            old.replace("dim=2x2", "dim=20x20", 1),
        ]
        for name, data_rows in (("old", [old] * 3), ("new", variants)):
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
        assert "logic-changed=2 layout-only=1" in result.stdout, result.stdout
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["kind"] for row in rows] == ["logic", "logic"], rows
        assert all("argument order changed" != row["summary"] for row in rows), rows


def main() -> int:
    test_disconnected_branch_changes_logic()
    test_edge_contact_is_not_layout_only()
    test_unknown_operand_changes_are_preserved()
    test_canvas_size_only_is_layout_only()
    test_element_movement_is_conservatively_reported()
    test_cli_reports_changes_in_default_output()
    print("6 semantic-diff regression checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
