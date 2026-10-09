from __future__ import annotations

"""Unverified w3pa strings must not become decoded refresh boundaries.

The candidate byte streams deliberately exercise the old reader's accepted
strings and +0x22 word. They are NOT GX Works3 exports or a binary schema
fixture. CLI tests pass them through the actual producer and consumers. The
station test independently fixes the contract for supplied, confirmed CPU
ranges; it does not validate the station record decoder or mapping formula.
"""

import csv
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_comm_detail import assignment_for_device, extract_remote_station_assignments
from gx3cli.gx3_external_inputs import read_refresh_areas
from gx3cli.gx3_synthetic_project import create_synthetic_project

REPO = Path(__file__).resolve().parents[1]


def run(module: str, args: list[str], cwd: Path, *, expect_success: bool = True) -> str:
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8")
    for key in ("PROJECT_ROOT", "GX3_ROOT", "PROJECT_COMM_PREFIX", "GX3_COMM_PREFIX",
                "PROJECT_OUTPUT_PREFIX", "GX3_OUTPUT_PREFIX"):
        env.pop(key, None)
    result = subprocess.run([sys.executable, "-B", "-m", module, *args],
                            cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", timeout=30)
    assert (result.returncode == 0) == expect_success, (result.stdout, result.stderr)
    return result.stdout


def unit_config(root: Path, ids: tuple[int, ...] = (10,)) -> None:
    # The product's synthetic generator leaves a text placeholder here.
    path = root / "UnitConfig.dat"
    path.write_bytes(b"")
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE Object(ObjectID INTEGER, ObjectName TEXT);
            CREATE TABLE Unit(ObjectID INTEGER, ObjectIDOfBaseUnit INTEGER,
                              SlotNumber INTEGER, IONumber INTEGER, IOOccupation INTEGER);
            CREATE TABLE NetworkUnit(ObjectID INTEGER, StationType INTEGER,
                                     StationNumber INTEGER, Mode INTEGER);
            CREATE TABLE PropertyConnectionPoint(ObjectID INTEGER, ConnectionTypeViewArray BLOB);
            CREATE TABLE ParameterUnit(ObjectID INTEGER, Identificationkey INTEGER);
        """)
        for slot, oid in enumerate(ids):
            con.execute("INSERT INTO Object VALUES (?,?)", (oid, "RJ61BT11"))
            con.execute("INSERT INTO Unit VALUES (?,?,?,?,?)", (oid, 1, slot, slot * 16, 32))


def stream(path: Path, entries: list[tuple[str, int | None]], owner: int = 10) -> None:
    data = bytearray("RJ61BT11".encode("utf-16le") + b"\0\0")
    data.extend(struct.pack("<H", owner) + b"\0" * 64)
    for device, word in entries:
        data.extend(device.encode("utf-16le") + b"\0\0")
        if word is not None:
            data.extend(b"\0" * 0x22 + struct.pack("<H", word) + b"\0" * 64)
    path.write_bytes(data)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def refresh(root: Path, work: Path, prefix: str) -> Path:
    run("gx3cli.gx3_cli", ["comm-refresh", "--root", str(root),
        "--output-dir", str(work), "--prefix", prefix], work)
    return work / f"{prefix}_refresh_areas.csv"


def test_candidates_through_cli_reader_detail_and_lint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "project")
        unit_config(root)
        entries = [("SB0", 16), ("SW0", 2), ("XA", 32), ("Y100", 32),
                   ("W100", 4), ("D200", 4), ("W300", 4), ("B100", 32),
                   ("M1000", 32), ("L2000", 32), ("R1000", 4), ("ZR2000", 4),
                   ("RD3000", 4), ("W900", 64), ("D1A", 4), ("M3000", 0),
                   ("M4000", None)]
        stream(root / "parameters.w3pa", entries)
        path = refresh(root, work, "candidate")
        rows = csv_rows(path)
        by_device = {r["device_start"]: r for r in rows}
        assert set(by_device) == {d for d, _ in entries if d != "D1A"}, rows
        for row in rows:
            assert row["record_state"] == "unverified_candidate"
            assert row["confidence"] == "string_only" and row["count_unit"] == "unknown"
            assert row["area_kind"] == row["direction"] == "unknown"
            assert all(row[k] == "" for k in ("device_end", "points_or_words", "object_id", "slot_number"))
        assert by_device["W900"]["candidate_points_or_words"] == "64"
        assert by_device["W900"]["candidate_device_end"] == "W93F"
        assert by_device["XA"]["candidate_device_end"] == "X29"
        assert by_device["M1000"]["candidate_device_end"] == "M1031"
        assert by_device["M3000"]["candidate_points_or_words"] == "0"
        assert by_device["M4000"]["candidate_points_or_words"] == ""
        manifest = json.loads((work / "candidate_manifest.json").read_text(encoding="utf-8"))
        assert manifest["counts"]["refresh_areas"] == 0
        assert manifest["counts"]["refresh_candidates"] == len(rows)
        evidence = read_refresh_areas(path)
        assert evidence.analysis.state == "partial" and not evidence.areas
        assert evidence.analysis.detail["unverified_count"] == len(rows)
        run("gx3cli.gx3_cli", ["w3pa-probe", "--root", str(root), "--output-dir", str(work), "--prefix", "probe"], work)
        probe = {r["device_start"]: r for r in csv_rows(work / "probe_devices.csv")}
        assert {"R1000", "ZR2000", "RD3000", "XA"}.issubset(probe)
        assert "D1A" not in probe and probe["XA"]["device_end_guess"] == "X29"
        assert all(r["confidence"] == "string_only" for r in probe.values())
        run("gx3cli.gx3_cli", ["comm-detail", "--root", str(root), "--refresh-csv", str(path),
            "--unit-csv", str(work / "candidate_units.csv"), "--output-dir", str(work), "--prefix", "detail"], work)
        assert not csv_rows(work / "detail_remote_station_assignments.csv")
        detail = json.loads((work / "detail_manifest.json").read_text(encoding="utf-8"))
        assert detail["refresh_area_analysis"]["state"] == "partial"
        db = work / "xref.sqlite"
        run("gx3cli.gx3_cli", ["xref", "build", "--root", str(root), "--db", str(db)], work)
        text = run("gx3cli.gx3_lint", [str(root), "--xref-db", str(db),
            "--refresh-csv", str(path), "--checks", "external-value-source", "--require-evaluated",
            "--format", "json", "--out-prefix", str(work / "lint")], work, expect_success=False)
        summary = json.loads(text)
        result = summary["checks"]["external-value-source"]
        assert result["state"] == "not_evaluated" and result["count"] == 0, result


def test_no_candidate_and_no_parameter_file_are_inconclusive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name, with_parameter in (("opaque", True), ("missing", False), ("module_only", True)):
            root = create_synthetic_project(work / name)
            unit_config(root)
            if with_parameter:
                (root / "parameters.w3pa").write_bytes(
                    b"\x01\x02\x03" if name == "opaque" else "RJ61BT11".encode("utf-16le") + b"\0\0")
            path = refresh(root, work, name)
            assert not csv_rows(path)
            evidence = read_refresh_areas(path)
            assert evidence.analysis.state == "partial" and not evidence.areas
            source = evidence.analysis.detail["refresh_extraction"]
            assert source["state"] == ("unsupported" if with_parameter else "not_evaluated")
            # An old/tampered manifest cannot turn an empty generated CSV into checked.
            manifest = work / f"{name}_manifest.json"
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["refresh_csv_sha256"] = "stale"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            assert "manifest_issue" in read_refresh_areas(path).analysis.detail
            manifest.write_text("{broken", encoding="utf-8")
            assert read_refresh_areas(path).analysis.state == "partial"


def test_filename_order_does_not_assign_units_and_single_candidate_is_kept() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "project")
        unit_config(root, (10, 20))
        stream(root / "a.w3pa", [("M100", 32)], owner=20)
        stream(root / "z.w3pa", [("R1000", 4)], owner=10)
        rows = csv_rows(refresh(root, work, "order"))
        assert len(rows) == 2 and {r["evidence_file"] for r in rows} == {"a.w3pa", "z.w3pa"}
        assert all(r["object_id"] == r["slot_number"] == r["network_label"] == "" for r in rows)


def test_legacy_guesses_are_excluded_but_supplied_ranges_remain_compatible() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "supplied.csv"
        path.write_text("device_start,device_end,confidence\nM1000,M1031,high\n"
                        "W900,W93F,high_string_evidence_manual_format_inference\n"
                        "D200,D203,medium_string_evidence_module_format_inference\n", encoding="utf-8")
        evidence = read_refresh_areas(path)
        assert [a.start_text for a in evidence.areas] == ["M1000"]
        assert evidence.analysis.state == "partial" and evidence.analysis.detail["unverified_count"] == 2
        path.write_text("device_start,device_end\nM1000,M1031\n", encoding="utf-8")
        assert read_refresh_areas(path).analysis.state == "checked"


def test_supplied_cpu_prefixes_survive_station_candidate_formula() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "project")
        name = "AJ65BT-R2N"
        data = name.encode("utf-16le") + struct.pack("<H", 1) + b"\0" * 62 + struct.pack("<H", 1) + b"\0" * 16
        (root / "station.w3pa").write_bytes(data)
        rows = [dict(evidence_file="station.w3pa", area_kind=kind, device_start=start, device_end=end)
                for kind, start, end in (
                    ("remote_input_RX", "M1000", "M1031"),
                    ("remote_output_RY", "L2000", "L2031"),
                    ("remote_register_RWr", "D100", "D103"),
                    ("remote_register_RWw", "RD200", "RD203"))]
        assignments = extract_remote_station_assignments(rows, root, [])
        assert len(assignments) == 1, assignments
        assignment = assignments[0]
        assert assignment["rx_range"] == "M1000..M1031" and assignment["station_rx_base"] == "M1000"
        assert assignment["ry_range"] == "L2000..L2031" and assignment["station_ry_base"] == "L2000"
        assert assignment["rwr_range"] == "D100..D103" and assignment["rww_range"] == "RD200..RD203"
        assert assignment["confidence"] == "unverified_standard_station_mapping"
        path = work / "supplied.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        run("gx3cli.gx3_cli", ["comm-detail", "--root", str(root), "--refresh-csv", str(path),
            "--unit-csv", str(work / "absent_units.csv"), "--output-dir", str(work), "--prefix", "station"], work)
        actual = csv_rows(work / "station_remote_station_assignments.csv")
        assert len(actual) == 1 and actual[0]["rx_range"] == "M1000..M1031", actual
        assert actual[0]["ry_range"] == "L2000..L2031" and actual[0]["rww_range"] == "RD200..RD203"
        assert actual[0]["confidence"] == "unverified_standard_station_mapping"
        assert assignment_for_device({"device": "M1000"}, assignments) is None
        # Independent lookup contract for already-confirmed assignments.
        confirmed = dict(assignment, confidence="confirmed")
        for device in ("M1000", "L2031", "D103", "RD203"):
            assert assignment_for_device({"device": device}, [confirmed]) is confirmed
        assert assignment_for_device({"device": "M1032"}, [confirmed]) is None
        assert not extract_remote_station_assignments(rows + [rows[0]], root, [])
        assert not extract_remote_station_assignments([dict(r, record_state="unverified_candidate") for r in rows], root, [])


if __name__ == "__main__":
    test_candidates_through_cli_reader_detail_and_lint()
    test_no_candidate_and_no_parameter_file_are_inconclusive()
    test_filename_order_does_not_assign_units_and_single_candidate_is_kept()
    test_legacy_guesses_are_excluded_but_supplied_ranges_remain_compatible()
    test_supplied_cpu_prefixes_survive_station_candidate_formula()
    print("refresh candidate and consumer regression tests passed")
