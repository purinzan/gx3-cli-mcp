from __future__ import annotations

"""What each intelligent function module was set to.

Every module keeps a parameter database in the project: one sqlite file per
unit, named by a long number. They all share a shape, and `ProfileTableInfo`
says what each table in them is:

    DEVICEINFO      the unit itself -- model, head I/O, base, slot, occupancy
    CARDINFO        the settings a technician typed, under PARAM_*Setting
    BASICPARAMETER  the parameter catalogue: descriptors, not values
    _UNITPARAM      refresh and handshake settings, one row per address

The distinction matters, and missing it is what made these files look
unreadable. A table named `RecordingBuffer` or `IPAddress` sounds like it holds
the setting; its rows are `Prm1=10, Prm2=31504, Prm3=257`, where 257 is the
same version number every such table carries and Prm2 is that table's id. The
values a technician entered are in `PARAM_BasicSetting`, and several of them
are plain text:

    BasePrm3 = <ip address>    BasePrm6 = 800

What is still missing is the name behind `BasePrm<n>`. That mapping lives in
the module profile GX Works3 installs, not in the project, so this reports the
number and the value and does not guess the meaning.

`DataArrayIndexX` is the channel or axis the row belongs to: an 8-channel
analog module repeats each setting eight times, a 16-axis motion module
sixteen.
"""

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root


MODULE_DB_RE = re.compile(r"^\d+\.db$")

# What ProfileTableInfo calls each kind of table.
VALUES = "CARDINFO"
CATALOGUE = "BASICPARAMETER"
IDENTITY = "DEVICEINFO"
REFRESH = "_UNITPARAM"

IDENTITY_LABELS = ("DeviceModel", "_HeadIO", "_BaseNo", "_SlotNo", "_OccupancyPoint", "Version")


@dataclass
class Setting:
    table: str
    label: str
    index: str
    value: str


@dataclass
class Module:
    path: Path
    model: str = ""
    identity: dict[str, str] = field(default_factory=dict)
    settings: list[Setting] = field(default_factory=list)
    catalogue_tables: list[str] = field(default_factory=list)
    refresh_rows: int = 0
    note: str = ""

    @property
    def head_io(self) -> str:
        raw = self.identity.get("_HeadIO", "")
        try:
            return f"0x{int(raw):X}"
        except (TypeError, ValueError):
            return raw

    @property
    def unit_number(self) -> str:
        """The U number the ladder uses for this unit's buffer memory."""
        try:
            return f"U{int(self.identity.get('_HeadIO', '')) // 16:X}"
        except (TypeError, ValueError):
            return ""


def table_kinds(con: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = con.execute("select Label, Data from ProfileTableInfo").fetchall()
    except sqlite3.Error:
        return {}
    return {str(label): str(data) for label, data in rows}


def is_catalogue(rows: list) -> bool:
    """A descriptor table, not one holding values.

    Every descriptor table carries the same shape: labels Prm1, Prm2, Prm3 ...
    with Prm3 set to 257, which is the profile version and is identical across
    every such table in every module. Prm2 is that table's id. Tables that
    describe a parameter rather than hold one always have it; tables that hold
    settings -- an analog module's per-channel AppliedSetting, a module's
    InitialOperationSetting -- never do.
    """
    version = {str(label): str(value) for label, _index, value, _default in rows}
    return version.get("Prm3") == "257"


def read_module(path: Path) -> Module:
    module = Module(path=path)
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        module.note = f"cannot open: {exc}"
        return module
    try:
        kinds = table_kinds(con)
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]

        for table in tables:
            kind = kinds.get(table, "")
            try:
                rows = con.execute(
                    f"select Label, DataArrayIndexX, Data, DataDefault from [{table}]"
                ).fetchall()
            except sqlite3.Error:
                continue

            if table == "DeviceInfo" or kind == IDENTITY:
                module.identity.update(
                    {str(label): str(value) for label, _index, value, _default in rows}
                )
                continue
            if kind == REFRESH or table == "_UnitParam":
                module.refresh_rows += len(rows)
                continue
            if kind == CATALOGUE and is_catalogue(rows):
                # A descriptor table: it says a parameter exists, not what it
                # was set to. Counted, not printed as a setting.
                module.catalogue_tables.append(table)
                continue

            for label, index, value, default in rows:
                if value in (None, "") or str(value) == str(default):
                    continue
                module.settings.append(
                    Setting(table=table, label=str(label), index=str(index), value=str(value))
                )
    finally:
        con.close()
    module.model = module.identity.get("DeviceModel", "")
    return module


def find_modules(root: Path) -> list[Module]:
    modules = []
    for path in sorted(root.glob("*.db")):
        if not MODULE_DB_RE.match(path.name):
            continue
        module = read_module(path)
        if module.model or module.settings:
            modules.append(module)
    return sorted(modules, key=lambda m: (m.identity.get("_BaseNo", ""), m.identity.get("_SlotNo", ""), m.model))


def as_text(modules: list[Module]) -> list[str]:
    out: list[str] = []
    for module in modules:
        head = f"{module.model or module.path.name}"
        where = f"base={module.identity.get('_BaseNo', '?')} slot={module.identity.get('_SlotNo', '?')}"
        unit = f" buffer={module.unit_number}\\G..." if module.unit_number else ""
        out.append(f"\n== {head}  {where} head_io={module.head_io}{unit}")
        out.append(f"   parameter db: {module.path.name}")
        if module.note:
            out.append(f"   {module.note}")
            continue
        if module.catalogue_tables:
            out.append(
                f"   parameter catalogue (descriptors, not values): "
                f"{', '.join(module.catalogue_tables[:8])}"
                + (" ..." if len(module.catalogue_tables) > 8 else "")
            )
        if module.refresh_rows:
            out.append(f"   refresh/handshake rows: {module.refresh_rows}")
        if not module.settings:
            out.append("   no setting differs from its default")
            continue
        by_table: dict[str, list[Setting]] = {}
        for setting in module.settings:
            by_table.setdefault(setting.table, []).append(setting)
        for table, settings in by_table.items():
            out.append(f"   [{table}] {len(settings)} set")
            for setting in settings[:12]:
                index = f" ch/axis {setting.index}" if setting.index not in ("", "1", "None") else ""
                out.append(f"      {setting.label:<24}{index:<12} = {setting.value[:48]}")
            if len(settings) > 12:
                out.append(f"      ... {len(settings) - 12} more")
    return out


def as_data(modules: list[Module]) -> list[dict[str, object]]:
    return [
        {
            "model": module.model,
            "parameter_db": module.path.name,
            "base": module.identity.get("_BaseNo", ""),
            "slot": module.identity.get("_SlotNo", ""),
            "head_io": module.head_io,
            "buffer_unit": module.unit_number,
            "catalogue_tables": module.catalogue_tables,
            "refresh_rows": module.refresh_rows,
            "settings": [
                {"table": s.table, "label": s.label, "index": s.index, "value": s.value}
                for s in module.settings
            ],
            "note": module.note,
        }
        for module in modules
    ]


def as_csv(modules: list[Module]) -> list[str]:
    rows = ["model,parameter_db,base,slot,head_io,table,label,index,value"]
    for module in modules:
        for s in module.settings:
            value = s.value.replace('"', '""')
            rows.append(
                f"{module.model},{module.path.name},{module.identity.get('_BaseNo','')},"
                f"{module.identity.get('_SlotNo','')},{module.head_io},{s.table},{s.label},"
                f'{s.index},"{value}"'
            )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report what each intelligent function module was set to."
    )
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--unit", default="", help="only modules whose model contains this")
    add_format_argument(parser, choices=("text", "json", "csv"), json_shorthand=False)
    args = parser.parse_args(argv)

    root = Path(args.root)
    modules = find_modules(root)
    if args.unit:
        wanted = args.unit.lower()
        modules = [m for m in modules if wanted in m.model.lower()]
    if not modules:
        print(f"no module parameter databases found under: {root}")
        return 1

    return emit(
        args,
        text=lambda: as_text(modules),
        data=lambda: as_data(modules),
        csv_text=lambda: as_csv(modules),
    )


if __name__ == "__main__":
    raise SystemExit(main())
