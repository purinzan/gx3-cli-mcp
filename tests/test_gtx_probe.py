from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from gx3cli.gtx_probe import analyze_file, collect_strings, device_hits_from_strings, iter_marker_hits


def main() -> int:
    data = (
        b"GT"
        + b"\x00" * 16
        + b"PK\x03\x04"
        + b"\x15\x02\x05\x0b" * 7
        + b"BAS012529on"
        + b" screen D100 M200"
        + b"\x00"
        + "運転画面".encode("utf-16le")
    )
    marker_counts, markers = iter_marker_hits(data, "sample.GTX", 10)
    if marker_counts["pk_local_like"] != 1:
        raise AssertionError("expected one PK-like marker")
    if marker_counts["base_screen"] != 1:
        raise AssertionError("expected one BAS marker")
    if not any(row.marker == "base_screen" for row in markers):
        raise AssertionError("expected emitted BAS marker context")

    strings, counts = collect_strings(
        data,
        "sample.GTX",
        min_string=4,
        max_strings=20,
        unicode_strings=True,
        deep_strings=False,
        all_strings=False,
    )
    if counts["ascii"] < 1:
        raise AssertionError("expected ASCII strings")
    if not any("BAS012529on" in row.text for row in strings):
        raise AssertionError("expected BAS string")
    if not any("運転画面" in row.text for row in strings):
        raise AssertionError("expected UTF-16LE Japanese string")

    devices = device_hits_from_strings(strings, {"D100": [("PLC_A", "test word")], "M200": [("PLC_A", "test bit")]})
    got = {row.normalized_device: row for row in devices}
    if "D100" not in got or "M200" not in got:
        raise AssertionError(f"missing device candidates: {sorted(got)}")
    if got["D100"].matched_device != "D100":
        raise AssertionError("expected PLC comment match for D100")

    with tempfile.TemporaryDirectory(prefix="gx3_gtx_probe_") as td:
        tmp = Path(td) / "_test_gtx_probe_sample.GTX"
        tmp.write_bytes(data)
        args = argparse.Namespace(
            max_marker_hits=10,
            min_string=4,
            max_strings_per_file=20,
            unicode_strings=True,
            deep_strings=False,
            all_strings=False,
        )
        stats, _, _, file_devices, records = analyze_file(tmp, args, {"D100": [("PLC_A", "test word")]})
    if not stats.gt_magic:
        raise AssertionError("expected GT magic")
    if stats.device_candidates < 2 or not any(row.matched_device == "D100" for row in file_devices):
        raise AssertionError("expected analyzed device candidates")
    if not any(row.label == "BAS012529on" for row in records):
        raise AssertionError("expected BAS record label")

    print("gtx probe regression checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
