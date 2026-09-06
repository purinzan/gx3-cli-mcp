from __future__ import annotations

import argparse
import json
import socket
import tempfile
import threading
from pathlib import Path

from gx3cli.gx3_ladder_print import load_live_values
from gx3cli.gx3_live_read import (
    LOG_REPLAY_SCOPE,
    build_3e_binary_read_frame,
    build_change_points,
    build_device_series,
    build_log_snapshot,
    decode_bit_values,
    explain_request,
    load_captured_log,
    parse_device,
    read_current_values,
)


def serve_once(response_payload: bytes, seen: list[bytes]) -> tuple[str, int, threading.Thread]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("localhost", 0))
    server.listen(1)
    host, port = server.getsockname()

    def run() -> None:
        try:
            conn, _addr = server.accept()
            with conn:
                data = conn.recv(1024)
                seen.append(data)
                response = b"\xD0\x00\x00\xFF\xFF\x03\x00" + (2 + len(response_payload)).to_bytes(2, "little")
                response += b"\x00\x00" + response_payload
                conn.sendall(response)
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return host, port, thread


def test_live_read_protocol() -> None:
    d100 = parse_device("D100")
    assert d100.prefix == "D"
    assert d100.number == 100
    assert parse_device("X1A").number == 0x1A

    frame = build_3e_binary_read_frame(d100, 2)
    assert frame[:2] == b"\x50\x00"
    assert frame[11:15] == b"\x01\x04\x00\x00"
    assert frame[15:18] == (100).to_bytes(3, "little")
    assert frame[18] == 0xA8
    assert frame[19:21] == (2).to_bytes(2, "little")

    assert decode_bit_values(bytes([0x10, 0x01]), 4) == [True, False, False, True]

    seen: list[bytes] = []
    host, port, thread = serve_once((123).to_bytes(2, "little") + (0xFFFF).to_bytes(2, "little"), seen)
    args = argparse.Namespace(
        ip=host,
        port=port,
        device="D100",
        count=2,
        type="signed-word",
        timeout=2.0,
        network=0,
        pc=0xFF,
        io=0x03FF,
        station=0,
        timer=0x0010,
        dry_run=False,
    )
    result = read_current_values(args)
    thread.join(2)
    assert result["values"] == [123, -1]
    assert seen and seen[0] == frame

    args.dry_run = True
    plan = read_current_values(args)
    assert plan["dry_run"] is True
    assert plan["request_hex"] == frame.hex(" ")
    assert explain_request(args)["device_code"] == "0xA8"


def test_csv_normalize_series_changes_and_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "capture.csv"
        path.write_text(
            "timestamp,device,value,value_type,source,project_fingerprint\n"
            "2026-09-06T10:00:00Z,M1,0,bit,poll,abc\n"
            "2026-09-06T10:00:00Z,D100,5,word,poll,abc\n"
            "2026-09-06T10:00:00Z,D100,6,word,poll,abc\n"
            "2026-09-06T10:00:01Z,M1,1,bit,poll,abc\n"
            "2026-09-06T10:00:03Z,D100,9,word,poll,abc\n",
            encoding="utf-8",
        )
        normalized = load_captured_log(path)
        assert normalized["scope"] == LOG_REPLAY_SCOPE
        assert normalized["metadata"]["input_records"] == 5
        assert normalized["metadata"]["normalized_records"] == 4
        assert normalized["metadata"]["duplicate_records_replaced"] == 1
        rows = normalized["records"]
        assert [row["timestamp"] for row in rows] == sorted(row["timestamp"] for row in rows)
        first_d100 = next(row for row in rows if row["timestamp"] == "2026-09-06T10:00:00Z" and row["device"] == "D100")
        assert first_d100["value"] == 6  # documented last-record-wins duplicate policy
        assert first_d100["value_type"] == "word"

        series = build_device_series(normalized, "m1")
        assert list(series["devices"]) == ["M1"]
        assert [row["value"] for row in series["devices"]["M1"]] == [False, True]

        changes = build_change_points(normalized, "M1")["changes"]
        assert len(changes) == 1
        assert changes[0]["previous_value"] is False
        assert changes[0]["value"] is True

        snapshot = build_log_snapshot(normalized, "2026-09-06T10:00:00Z")
        assert snapshot["values"] == {"D100": 6, "M1": False}
        assert "D101" not in snapshot["values"]
        assert snapshot["metadata"]["value_types"] == {"D100": "word", "M1": "bit"}
        assert snapshot["metadata"]["carry_forward"] is False
        assert snapshot["metadata"]["scan_synchronized"] is False
        assert snapshot["project_fingerprint"] == "abc"

        live_path = root / "live.json"
        live_path.write_text(json.dumps(snapshot), encoding="utf-8")
        assert load_live_values(str(live_path)) == {"D100": 6, "M1": False}

        # 10:00:02 is equally close to :01 and :03; ties deterministically use
        # the earlier timestamp and do not fill D100 from the previous capture.
        nearest = build_log_snapshot(normalized, "2026-09-06T10:00:02Z", mode="nearest")
        assert nearest["timestamp"] == "2026-09-06T10:00:01Z"
        assert nearest["values"] == {"M1": True}
        assert "D100" not in nearest["values"]


def test_json_types_and_fingerprint_mismatch_are_visible() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "capture.json"
        path.write_text(
            json.dumps(
                {
                    "metadata": {"source": "export", "project_fingerprint": "deadbeef"},
                    "records": [
                        {
                            "timestamp": "2026-09-06T06:00:00-04:00",
                            "device": "D20",
                            "value": "0007",
                            "value_type": "vendor-word",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        normalized = load_captured_log(path)
        row = normalized["records"][0]
        assert row["timestamp"] == "2026-09-06T10:00:00Z"
        assert row["value"] == "0007"  # unknown vendor type is preserved, not guessed
        assert row["value_type"] == "vendor-word"
        assert row["source"] == "export"

        project = root / "project"
        project.mkdir()
        (project / "CPU.PRM").write_bytes(b"synthetic parameters")
        snapshot = build_log_snapshot(normalized, "2026-09-06T10:00:00Z", project_root=project)
        assert snapshot["metadata"]["fingerprint_match"] is False
        assert any("does not match" in warning for warning in snapshot["metadata"]["warnings"])


def test_mixed_capture_identity_is_rejected_before_deduplication() -> None:
    from gx3cli.gx3_live_read import log_replay_main
    from contextlib import redirect_stderr
    import io

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "capture.json"
        base = {"timestamp": "2026-09-06T10:00:00Z", "device": "M1", "value": False,
                "source": "PLC-A", "project_fingerprint": "A"}
        for changes in (
            {"source": "PLC-B"}, {"project_fingerprint": "B"},
            {"source": ""}, {"project_fingerprint": ""},
            {"source": "PLC-B", "timestamp": "2026-09-06T10:00:01Z"},
        ):
            path.write_text(json.dumps({"records": [base, {**base, "value": True, **changes}]}), encoding="utf-8")
            for mode in ("normalize", "series", "changes", "snapshot"):
                args = [mode, str(path)]
                if mode == "snapshot":
                    args += ["--at", base["timestamp"]]
                stderr = io.StringIO()
                with redirect_stderr(stderr):
                    assert log_replay_main(args) == 2, (mode, changes)
                assert "mixed capture identity" in stderr.getvalue()


def test_capture_identity_inheritance_and_legacy_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "capture.json"
        row = {"timestamp": "2026-09-06T10:00:00Z", "device": "M1", "value": False}
        for metadata in ({}, {"source": "PLC-A", "project_fingerprint": "A"}):
            path.write_text(json.dumps({"metadata": metadata, "records": [row, {**row, **metadata, "value": True}]}), encoding="utf-8")
            result = load_captured_log(path)
            assert result["metadata"]["duplicate_records_replaced"] == 1
            assert result["records"][0]["value"] is True
        csv = Path(tmp) / "capture.csv"
        csv.write_text("timestamp,device,value,source\n2026-09-06T10:00:00Z,M1,0,PLC-A\n2026-09-06T10:00:01Z,M1,1,PLC-B\n", encoding="utf-8")
        try:
            load_captured_log(csv)
        except ValueError as exc:
            assert "mixed capture identity" in str(exc)
        else:
            raise AssertionError("mixed CSV accepted")


def main() -> None:
    test_live_read_protocol()
    test_csv_normalize_series_changes_and_snapshot()
    test_json_types_and_fingerprint_mismatch_are_visible()
    test_mixed_capture_identity_is_rejected_before_deduplication()
    test_capture_identity_inheritance_and_legacy_input()
    print("live-read and captured-log replay checks passed")


if __name__ == "__main__":
    main()
