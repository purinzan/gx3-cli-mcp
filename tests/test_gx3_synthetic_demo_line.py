from __future__ import annotations

"""The demo-line fixture is what a new user runs first and what a bug report is
reproduced on, so its shape is worth pinning down."""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_synthetic_project import (
    DEMO_COMMENTS,
    STATION_COUNT,
    _device,
    _title_data,
    create_demo_line_project,
)
from gx3cli.extract_gx3_extended_instruction_knowledge import extract_title_text


def test_hex_numbered_types_round_trip() -> None:
    # GX numbers X/Y/B in hex, so X24 must be stored as 0x24 and not as 24.
    assert _device("X24") == "X36"
    assert _device("Y3D") == "Y61"
    assert _device("X0") == "X0"
    # Decimal-numbered types are left alone.
    assert _device("M100") == "M100"
    assert _device("SM400") == "SM400"


def test_section_titles_are_readable_back() -> None:
    # A title the extractor cannot read leaves the project with zero sections,
    # which is how this format was wrong before.
    assert extract_title_text(_title_data("Safety and mode selection")) == "Safety and mode selection"


def test_demo_line_project_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = create_demo_line_project(Path(tmp) / "line", overwrite=True)

        lddbs = sorted(root.glob("*_LDDB.db"))
        assert len(lddbs) >= 4, "the fixture should span several programs"

        rungs = 0
        titles = 0
        for lddb in lddbs:
            con = sqlite3.connect(lddb)
            rungs += con.execute("select count(*) from LadderBlocks where blocktype = 0").fetchone()[0]
            for (data,) in con.execute("select data from LadderBlocks where blocktype = 1"):
                assert extract_title_text(data), f"unreadable section title in {lddb.name}"
                titles += 1
            con.close()

        # Roughly 500 rungs: enough that section filtering and xref lookups have
        # something to do. Bounds are loose so tuning the fixture is not a test
        # change, but a collapse to a handful of rungs still fails.
        assert 400 <= rungs <= 600, rungs
        assert titles >= 5 * STATION_COUNT

        con = sqlite3.connect(root / "001_DC.db")
        commented = con.execute("select count(*) from COMMENT_DATA").fetchone()[0]
        con.close()
        assert commented >= len(DEMO_COMMENTS) + STATION_COUNT


def test_every_commented_device_is_unique() -> None:
    # A device listed twice would give it two comments and make every answer
    # about it ambiguous.
    devices = [_device(name) for name, _comment in DEMO_COMMENTS]
    assert len(devices) == len(set(devices))


def main() -> int:
    test_hex_numbered_types_round_trip()
    test_section_titles_are_readable_back()
    test_demo_line_project_shape()
    test_every_commented_device_is_unique()
    print("synthetic demo-line checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
