from __future__ import annotations

import argparse
import io
import sqlite3
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from gx3cli.gx3_index_lite import DEVICE_NAMING, create_schema, device_map, occupied_intervals


def add_device(con: sqlite3.Connection, device: str, device_type: str, number: int) -> None:
    con.execute(
        """
        insert into devices(
            device, device_type, number, comment, occurrences, driver_rows,
            condition_uses, roles, first_lddb, first_pos, first_title
        ) values (?, ?, ?, '', 1, 1, 0, '', '', 0, '')
        """,
        (device, device_type, number),
    )


def make_index(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    create_schema(con)
    con.execute("insert into meta(key, value) values ('device_naming', ?)", (DEVICE_NAMING,))
    return con


def test_covered_write_members_are_not_reported_free() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_device_map_") as tmp:
        db = Path(tmp) / "index.sqlite"
        con = make_index(db)
        add_device(con, "D400", "D", 400)
        add_device(con, "D410", "D", 410)
        con.execute(
            """
            insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
            values ('D', 400, 4, 'write', 'BMOV', 'SYNTH', 1)
            """
        )
        con.commit()

        assert occupied_intervals(con)["D"] == [(400, 403), (410, 410)]
        con.close()

        out = io.StringIO()
        args = argparse.Namespace(db=str(db), root="", min_free=1, types="D", max_gaps=8)
        with redirect_stdout(out):
            assert device_map(args) == 0
        text = out.getvalue()

        assert "D404-D409(6)" in text, text
        assert "D401" not in text and "D402" not in text and "D403" not in text, text
        assert "5" in text, text  # D400..D403 plus D410 are physically occupied.


def test_read_only_covered_range_is_also_occupied_for_reuse() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_device_map_read_") as tmp:
        db = Path(tmp) / "index.sqlite"
        con = make_index(db)
        add_device(con, "D500", "D", 500)
        add_device(con, "D510", "D", 510)
        con.execute(
            """
            insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
            values ('D', 503, 3, 'read', 'BMOV', 'SYNTH', 2)
            """
        )
        con.commit()

        assert occupied_intervals(con)["D"] == [(500, 500), (503, 505), (510, 510)]
        con.close()

        out = io.StringIO()
        args = argparse.Namespace(db=str(db), root="", min_free=1, types="D", max_gaps=8)
        with redirect_stdout(out):
            device_map(args)
        text = out.getvalue()

        assert "D501-D502(2)" in text, text
        assert "D506-D509(4)" in text, text
        assert "D503-D505" not in text, text


def test_overlapping_named_and_covered_ranges_count_once() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_device_map_overlap_") as tmp:
        db = Path(tmp) / "index.sqlite"
        con = make_index(db)
        add_device(con, "D601", "D", 601)
        con.executemany(
            """
            insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
            values ('D', ?, ?, 'write', 'BMOV', 'SYNTH', ?)
            """,
            [(600, 4, 1), (602, 4, 2)],
        )
        con.commit()
        assert occupied_intervals(con)["D"] == [(600, 605)]
        con.close()


def main() -> int:
    test_covered_write_members_are_not_reported_free()
    test_read_only_covered_range_is_also_occupied_for_reuse()
    test_overlapping_named_and_covered_ranges_count_once()
    print("device-map range checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
