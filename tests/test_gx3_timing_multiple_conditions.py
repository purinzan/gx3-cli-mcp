from __future__ import annotations

import tempfile
from pathlib import Path

from gx3cli.gx3_timing_chart import device_reader_condition, device_writer_condition
from test_gx3_timing_detect import create_xref


def test_multiple_writer_rows_are_all_rendered_as_alternatives() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_timing_multi_writer_") as tmp:
        db = Path(tmp) / "xref.sqlite"
        create_xref(
            db,
            [
                ("M10", "read", "a", {"pos": 1000, "comment": "auto mode"}),
                ("M100", "write", "c", {"pos": 1000, "comment": "request"}),
                ("M11", "read", "a", {"pos": 2000, "comment": "manual mode"}),
                ("M100", "write", "c", {"pos": 2000, "comment": "request"}),
            ],
        )

        import sqlite3

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = device_writer_condition(con, "M100")
        finally:
            con.close()

        assert "ALT 1" in text and "ALT 2" in text, text
        assert "M10" in text and "M11" in text, text
        assert ":1000:" in text and ":2000:" in text, text
        assert "scan order" in text, text


def test_multiple_reader_rows_are_all_rendered_without_fake_boolean_merge() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_timing_multi_reader_") as tmp:
        db = Path(tmp) / "xref.sqlite"
        create_xref(
            db,
            [
                ("B300", "read", "a", {"pos": 3000, "comment": "request in state A"}),
                ("M30", "read", "a", {"pos": 3000, "comment": "state A"}),
                ("B300", "read", "a", {"pos": 4000, "comment": "request in state B"}),
                ("M40", "read", "a", {"pos": 4000, "comment": "state B"}),
            ],
        )

        import sqlite3

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = device_reader_condition(con, "B300")
        finally:
            con.close()

        assert "ALT 1" in text and "ALT 2" in text, text
        assert "M30" in text and "M40" in text, text
        assert ":3000:" in text and ":4000:" in text, text
        assert "not combined into one Boolean condition" in text, text


def test_single_writer_keeps_the_simple_condition_shape() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_timing_single_writer_") as tmp:
        db = Path(tmp) / "xref.sqlite"
        create_xref(
            db,
            [
                ("M50", "read", "a", {"pos": 5000, "comment": "only condition"}),
                ("M500", "write", "c", {"pos": 5000, "comment": "only writer"}),
            ],
        )

        import sqlite3

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = device_writer_condition(con, "M500")
        finally:
            con.close()

        assert "M50" in text, text
        assert "ALT " not in text, text
        assert "multiple writer rows" not in text, text


def main() -> int:
    test_multiple_writer_rows_are_all_rendered_as_alternatives()
    test_multiple_reader_rows_are_all_rendered_without_fake_boolean_merge()
    test_single_writer_keeps_the_simple_condition_shape()
    print("timing multiple-condition checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
