from __future__ import annotations

"""project-config reads the half of a project that is not the ladder.

Its value is as much in what it refuses to claim as in what it reports. A
project that stores no device memory, an MES module whose job list was never in
the project, an encrypted body -- each is a different thing from a gap in this
tool, and a report that treats them alike sends someone looking for hours.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_project_config import (
    TOPICS,
    collect,
    head_io_and_unit,
    limits_section,
)


def make_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Config.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<Project>\n'
        '  <Config Unit="R32EN" UnitId="4107" />\n</Project>\n',
        encoding="utf-8",
    )
    # An encrypted body: present, and never readable.
    (root / "_Project.txc").write_bytes(bytes(range(256)) * 4)

    con = sqlite3.connect(root / "9055019884521717707.db")
    for name in ("DeviceInfo", "ProfileTableInfo", "LogicalSwitch_BasicSetting"):
        con.executescript(
            f"create table {name} (Label text, DataArrayIndexX text, DataArrayIndexY text,"
            " Data text, DataDefault text, ParamGroup text);"
        )
    con.executemany(
        "insert into DeviceInfo(Label, DataArrayIndexX, Data) values (?, ?, ?)",
        [("DeviceModel", "1", "RD81MES96N"), ("_HeadIO", "1", "2304"), ("_BaseNo", "1", "1"), ("_SlotNo", "1", "0")],
    )
    con.executemany(
        "insert into ProfileTableInfo(Label, Data) values (?, ?)",
        [("DeviceInfo", "DEVICEINFO"), ("LogicalSwitch_BasicSetting", "CARDINFO")],
    )
    con.executemany(
        "insert into LogicalSwitch_BasicSetting(Label, DataArrayIndexX, Data, DataDefault) values (?, ?, ?, ?)",
        [("BasePrm1", "1", "0", ""), ("BasePrm7", "1", "15", "")],
    )
    con.commit()
    con.close()


def test_a_head_io_gives_the_buffer_memory_unit_number() -> None:
    # UnitConfig.dat stores it as a decimal; the ladder spells it U30\G...
    assert head_io_and_unit(768) == ("0x300", "U30")
    assert head_io_and_unit("0x300") == ("0x300", "U30")
    assert head_io_and_unit(2400) == ("0x960", "U96")
    assert head_io_and_unit("") == ("", "")
    assert head_io_and_unit("not a number") == ("not a number", "")


def test_an_absent_device_memory_is_reported_as_absent_not_unreadable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        make_project(root)
        sections = {s.topic: s for s in collect(root, TOPICS)}

        limits = " ".join(sections["cpu"].limits)
        assert "device memory" in limits, limits
        assert "stores none" in limits, limits
        assert "Not a gap in the tool" in limits, limits


def test_the_mes_module_says_its_jobs_are_not_in_the_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        make_project(root)
        sections = {s.topic: s for s in collect(root, TOPICS)}

        limits = " ".join(sections["modules"].limits)
        assert "MES job definitions" in limits, limits
        assert "SD card" in limits, limits
        # And the reason the values cannot be called "changed".
        assert "DataDefault is empty" in limits, limits


def test_an_encrypted_body_is_named_once_with_its_reason() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        make_project(root)
        sections = collect(root, TOPICS)
        limits = limits_section(root, sections)
        text = " ".join(limits.lines)
        assert "_Project.txc" in text, text
        assert "encrypted" in text, text


def test_every_topic_builds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        make_project(root)
        for topic in TOPICS:
            sections = collect(root, (topic,))
            assert sections, topic
            assert sections[0].topic == topic


def main() -> int:
    test_a_head_io_gives_the_buffer_memory_unit_number()
    test_an_absent_device_memory_is_reported_as_absent_not_unreadable()
    test_the_mes_module_says_its_jobs_are_not_in_the_project()
    test_an_encrypted_body_is_named_once_with_its_reason()
    test_every_topic_builds()
    print("project config checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
