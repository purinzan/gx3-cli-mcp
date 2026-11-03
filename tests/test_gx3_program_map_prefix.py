"""Invented project metadata through load_program_map and the real CLI."""
from __future__ import annotations

import csv
import json
import sqlite3
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_program_map import BACKUP_MARKER, QPG_NAME_MARKER, load_program_map
from gx3cli.gx3_scan_order import build_pou_order
from gx3cli.gx3_synthetic_project import create_synthetic_project


def fixture(root: Path, groups, programs, *, generation="fixed") -> Path:
    create_synthetic_project(root)
    (root / "001_LDDB.db").unlink()
    # The generic generator writes a text placeholder for unit configuration.
    # exec-config needs its actual SQLite container, even when no units exist.
    (root / "UnitConfig.dat").unlink()
    with sqlite3.connect(root / "UnitConfig.dat") as con:
        con.execute("create table Object(ObjectID integer, ObjectName text)")
    (root / "CPU.PRM").write_bytes(("$MELPRJ$\0" + "\0".join(programs) + "\0").encode("utf-16-le"))
    index = 0
    for group_index, names in enumerate(groups, 1):
        program_dir = root / "ConvertData" / str(200000000000000 + group_index)
        program_dir.mkdir(parents=True)
        ids, records = [], []
        for name in names:
            index += 1
            decimal = str(100000000000000 + index)
            ids.append(decimal)
            guid = str(uuid.UUID(int=index))
            data, size, _ = generate_rung({"device": f"M{100 + index}"}, {"type": "coil", "device": "M900"})
            with sqlite3.connect(root / f"A{index:02}_LDDB.db") as con:
                con.execute("create table LadderBlocks(id text, pos real, blocktype integer, data text, rowsize integer, translated integer, ConvTarget integer)")
                con.execute("insert into LadderBlocks values (?,0,0,?,?,0,0)", ("_guid/" + guid, data, size))
            with sqlite3.connect(root / f"{decimal}_StepInfo.db") as con:
                con.execute("create table T_Block(Pos real, BlockID text, StepSize integer)")
                con.execute("insert into T_Block values (0,?,1)", ("{" + guid.upper() + "}",))
            raw = (name + "\0").encode("utf-16-le")
            record = struct.pack("<H", len(raw)) + raw
            records.append((QPG_NAME_MARKER + b"\0\0\xff\xff\xff\xff" if generation == "fixed" else b"\0\0") + record)
        (program_dir / "PouLinkOrder.info").write_text("\n".join(ids), encoding="ascii")
        (program_dir / "Program.qpg").write_bytes(b"".join(records) + (BACKUP_MARKER if generation == "backup" else b""))
    return root


def test_exact_other_program_shadows_the_prefix_and_no_fallback_restores_it():
    for generation in ("fixed", "backup"):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(Path(tmp) / "source", [["DemoUnit"], ["DemoUnitActive"]], ["DemoUnitActive", "SpareTask"], generation=generation)
            pm = load_program_map(root)
            assert pm.pous["A01"].name == "DemoUnit"
            assert pm.pous["A01"].program_file == "", pm.pous
            assert pm.pous["A02"].program_file == "DemoUnitActive"
            assert any("prefix" in warning and "DemoUnit" in warning for warning in pm.warnings), pm.warnings
            done = subprocess.run([sys.executable, "-m", "gx3cli.gx3_cli", "program-map", "--root", str(root), "--json"],
                                  capture_output=True, text=True, encoding="utf-8")
            assert done.returncode == 0, done.stdout + done.stderr
            payload = json.loads(done.stdout)
            assert {p["lddb_hex"]: p["program_file"] for p in payload["pous"]} == {"A01": "", "A02": "DemoUnitActive"}
            assert payload["warnings"]


def test_all_members_of_shadowed_group_stay_unassigned():
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(Path(tmp) / "source", [["DemoUnit", "LegacyChild"], ["DemoUnitActive", "ActiveChild"]], ["DemoUnitActive", "LegacyChildTask"])
        pm = load_program_map(root)
        assert [pm.pous[f"A{i:02}"].program_file for i in (1, 2)] == ["", ""], pm.pous
        assert [pm.pous[f"A{i:02}"].program_file for i in (3, 4)] == ["DemoUnitActive"] * 2


def test_prefix_compatibility_without_a_competing_exact_group():
    for names, program in [(["DemoUnit"], "DemoUnitActive"), (["DemoUnitExtra"], "DemoUnit"),
                           (["DemoUnit", "DemoUnitActive"], "DemoUnitActive")]:
        with tempfile.TemporaryDirectory() as tmp:
            pm = load_program_map(fixture(Path(tmp) / "source", [names], [program]))
            assert all(info.program_file == program for info in pm.pous.values()), pm.pous
            assert not pm.warnings, pm.warnings


def test_a_display_fallback_is_not_decoded_exact_name_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(Path(tmp) / "source", [["DemoUnit"], ["DemoUnitActive"]], ["DemoUnitActive"])
        qpg = root / "ConvertData" / "200000000000002" / "Program.qpg"
        qpg.write_bytes("DemoUnitActive\0".encode("utf-16-le"))
        pm = load_program_map(root)
        assert pm.pous["A02"].name == "DemoUnitActive"  # filled for display, not decoded
        assert pm.pous["A01"].program_file == "DemoUnitActive"
        assert not any("dropped prefix" in warning for warning in pm.warnings)


def test_execution_reports_do_not_restore_a_rejected_prefix():
    with tempfile.TemporaryDirectory() as tmp:
        root = fixture(Path(tmp) / "source", [["DemoUnit"], ["DemoUnit_Active"]], ["DemoUnit_Active"])
        pm = load_program_map(root)
        assert pm.pous["A01"].program_file == ""
        order = build_pou_order(root)
        assert order["DemoUnit"].program_file == "", order["DemoUnit"]
        assert order["DemoUnit"].exec_order is None
        assert order["DemoUnit"].confidence == "fallback"
        assert order["DemoUnit_Active"].confidence == "exec"
        done = subprocess.run([sys.executable, "-m", "gx3cli.gx3_cli", "exec-config", "--root", str(root),
                               "--output-dir", tmp, "--prefix", "execution"],
                              capture_output=True, text=True, encoding="utf-8")
        assert done.returncode == 0, done.stdout + done.stderr
        with (Path(tmp) / "execution_programs.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = {row["pou"]: row for row in csv.DictReader(stream)}
        assert rows["DemoUnit"]["program_file"] == "", rows
        assert rows["DemoUnit"]["exec_order"] == ""
        assert rows["DemoUnit_Active"]["program_file"] == "DemoUnit_Active"


def main():
    for name, test in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(test):
            test()
            print("PASS", name)


if __name__ == "__main__":
    main()
