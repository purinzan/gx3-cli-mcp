from __future__ import annotations

"""An intelligent function module's settings are in the project, and readable.

They looked unreadable because a table named `RecordingBuffer` or `IPAddress`
sounds like it holds the setting, and its rows are `Prm1=10, Prm2=31504,
Prm3=257`. Those tables are the parameter catalogue: descriptors saying a
parameter exists. `Prm3=257` is the profile version, identical in every such
table in every module, which is what tells them apart from a table holding
values. The values a technician entered are under `PARAM_*Setting`, and some
are plain text.

What is still not in the project is the name behind `BasePrm<n>`. That mapping
is in the module profile GX Works3 installs, so the command reports the number
and the value and does not guess.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_module_params import find_modules, is_catalogue, read_module


SCHEMA = """
create table {name} (
    Label text, DataArrayIndexX text, DataArrayIndexY text,
    Data text, DataDefault text, ParamGroup text
);
"""


def make_module(path: Path) -> None:
    con = sqlite3.connect(path)
    for name in (
        "DeviceInfo",
        "ProfileTableInfo",
        "PARAM_BasicSetting",
        "IPAddress",
        "AppliedSetting",
        "_UnitParam",
    ):
        con.executescript(SCHEMA.format(name=name))

    con.executemany(
        "insert into DeviceInfo(Label, DataArrayIndexX, Data) values (?, ?, ?)",
        [("DeviceModel", "1", "RD81RC96"), ("_HeadIO", "1", "2368"), ("_BaseNo", "1", "1"), ("_SlotNo", "1", "3")],
    )
    con.executemany(
        "insert into ProfileTableInfo(Label, Data) values (?, ?)",
        [
            ("DeviceInfo", "DEVICEINFO"),
            ("PARAM_BasicSetting", "CARDINFO"),
            ("IPAddress", "BASICPARAMETER"),
            ("AppliedSetting", "BASICPARAMETER"),
            ("_UnitParam", "_UNITPARAM"),
        ],
    )
    # What the technician set.
    con.executemany(
        "insert into PARAM_BasicSetting(Label, DataArrayIndexX, Data, DataDefault) values (?, ?, ?, ?)",
        [("BasePrm3", "1", "192.0.2.10", ""), ("BasePrm6", "1", "800", ""), ("BasePrm7", "1", "0", "0")],
    )
    # A descriptor table: Prm3 is the profile version.
    con.executemany(
        "insert into IPAddress(Label, DataArrayIndexX, Data, DataDefault) values (?, ?, ?, ?)",
        [("Prm1", "1", "18", ""), ("Prm2", "1", "31488", ""), ("Prm3", "1", "257", "")],
    )
    # A settings table that happens to be filed under BASICPARAMETER: an
    # eight-channel module repeats one setting per channel, and carries no
    # profile version.
    con.executemany(
        "insert into AppliedSetting(Label, DataArrayIndexX, Data, DataDefault) values (?, ?, ?, ?)",
        [("AppliedPrm5", str(ch), "1", "0") for ch in range(1, 9)],
    )
    con.executemany(
        "insert into _UnitParam(Label, DataArrayIndexX, Data) values (?, ?, ?)",
        [("_RWTimming", str(i), "1") for i in range(1, 5)],
    )
    con.commit()
    con.close()


def test_a_descriptor_table_is_told_from_one_holding_values() -> None:
    catalogue = [("Prm1", "1", "18", ""), ("Prm2", "1", "31488", ""), ("Prm3", "1", "257", "")]
    assert is_catalogue(catalogue)

    per_channel = [("AppliedPrm5", str(ch), "1", "0") for ch in range(1, 9)]
    assert not is_catalogue(per_channel)

    # Same label shape, no profile version: settings, not a catalogue.
    initial_operation = [("Prm1", "1", "0", ""), ("Prm3", "1", "0", ""), ("Prm5", "1", "1", "")]
    assert not is_catalogue(initial_operation)


def test_the_settings_come_out_and_the_catalogue_does_not() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "3010658289117734017.db"
        make_module(path)
        module = read_module(path)

        assert module.model == "RD81RC96"
        assert module.head_io == "0x940"
        # The ladder reaches this unit's buffer memory as U94\G...
        assert module.unit_number == "U94"

        values = {(s.table, s.label, s.index): s.value for s in module.settings}
        assert values[("PARAM_BasicSetting", "BasePrm3", "1")] == "192.0.2.10"
        assert values[("PARAM_BasicSetting", "BasePrm6", "1")] == "800"
        # Unchanged from its default: not a setting anyone made.
        assert ("PARAM_BasicSetting", "BasePrm7", "1") not in values

        # The per-channel table is settings, one row per channel.
        channels = sorted(s.index for s in module.settings if s.table == "AppliedSetting")
        assert channels == [str(ch) for ch in range(1, 9)], channels

        # The descriptor table is counted, not reported as a setting.
        assert module.catalogue_tables == ["IPAddress"], module.catalogue_tables
        assert not any(s.table == "IPAddress" for s in module.settings)

        assert module.refresh_rows == 4


def test_only_module_databases_are_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        make_module(root / "3010658289117734017.db")
        # A ladder database is not a module parameter database.
        sqlite3.connect(root / "abc123_LDDB.db").close()
        modules = find_modules(root)
        assert [m.model for m in modules] == ["RD81RC96"], [m.path.name for m in modules]


def main() -> int:
    test_a_descriptor_table_is_told_from_one_holding_values()
    test_the_settings_come_out_and_the_catalogue_does_not()
    test_only_module_databases_are_read()
    print("module params checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
