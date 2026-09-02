from __future__ import annotations

"""Resolve the label references a ladder row carries, into names.

A label-based program spells its contacts and coils as "_lid/<LabelID>/<row>"
rather than as a device. The decoder recognised those as label references and
counted them, so the rung structure came out right, but it never looked up what
they named -- every occurrence arrived with no identity at all. On a project
written with labels that left the cross-reference empty, and with it every tool
that reads the cross-reference, while the parse still reported itself as exact.

LabelData.db holds the answer beside the program:

    LabelTbl        one row per label table (LabelID)
    RowTbl          one row per label in it (RowID, RowNo)
    ColumnDataTbl   the columns of that row -- class, name, type, comment
    DeviceAssignTbl where the label ended up, when it has an address

The ladder's second path element matches RowNo, which equals RowID for every
row except the EN/ENO pins, so both are accepted as keys.

A local label has no device of its own: the compiler places it in label memory
and DeviceAssignTbl records that as "LV:0.0". Only a global label assigned to a
real device carries something like "X0". Either way the label name is the
identity the program was written in, so that is what an occurrence is given;
the assignment is recorded alongside it rather than replacing it, since one row
of a structure label can cover several devices.
"""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

# ColumnDataTbl.ColumnID, from ColumnDefineMst's ordering.
_COL_CLASS = 1
_COL_NAME = 2
_COL_DATA_TYPE = 3
_COL_COMMENT = 15


@dataclass(frozen=True)
class LabelRef:
    """One label, as the program names it."""

    name: str
    label_class: str = ""
    data_type: str = ""
    comment: str = ""
    # Devices the label was assigned. Empty for a label the compiler placed in
    # label memory without recording an address; several for a structure.
    devices: tuple[str, ...] = field(default=())

    @property
    def detail(self) -> str:
        """A short note for the occupancy record, naming class and address."""
        parts = [p for p in (self.label_class, ", ".join(self.devices)) if p]
        return " ".join(("label",) + tuple(parts))


class LabelResolver:
    """Lookup from (label table id, row) to the label it names."""

    def __init__(self, entries: dict[tuple[str, int], LabelRef]) -> None:
        self._entries = entries

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, label_id: str, row: int) -> LabelRef | None:
        return self._entries.get((str(label_id), int(row)))

    def resolve_token(self, token: str) -> LabelRef | None:
        """Resolve a header token spelled "_lid/<LabelID>/<row>"."""
        parsed = split_label_token(token)
        if parsed is None:
            return None
        return self.get(*parsed)


def split_label_token(token: str) -> tuple[str, int] | None:
    """Split "_lid/<LabelID>/<row>" into its parts. None if it is not one."""
    if not token.startswith("_lid/"):
        return None
    parts = token.split("/")
    if len(parts) != 3:
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


EMPTY = LabelResolver({})


def load_label_resolver(root: Path) -> LabelResolver:
    """Read LabelData.db under root. Empty resolver when there is none.

    A project with no labels has no LabelData.db, and a project this cannot
    read should lose its label names rather than fail the whole run, so every
    failure here degrades to the empty resolver.
    """
    path = Path(root) / "LabelData.db"
    if not path.exists():
        return EMPTY
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return EMPTY
    try:
        return LabelResolver(_read_entries(con))
    except sqlite3.Error:
        return EMPTY
    finally:
        con.close()


def _read_entries(con: sqlite3.Connection) -> dict[tuple[str, int], LabelRef]:
    columns: dict[tuple[str, int], dict[int, str]] = {}
    for label_id, row_id, column_id, value in con.execute(
        "select LabelID, RowID, ColumnID, ColumnStrValue from ColumnDataTbl"
    ):
        if value:
            columns.setdefault((str(label_id), int(row_id)), {})[int(column_id)] = str(value)

    devices: dict[tuple[str, int], list[str]] = {}
    for label_id, row_id, device in con.execute(
        "select LabelID, RowID, MELSECDevice from DeviceAssignTbl"
    ):
        if device:
            devices.setdefault((str(label_id), int(row_id)), []).append(str(device))

    entries: dict[tuple[str, int], LabelRef] = {}
    for label_id, row_id, row_no in con.execute("select LabelID, RowID, RowNo from RowTbl"):
        key = (str(label_id), int(row_id))
        cols = columns.get(key, {})
        name = cols.get(_COL_NAME, "")
        if not name:
            continue
        ref = LabelRef(
            name=name,
            label_class=cols.get(_COL_CLASS, ""),
            data_type=cols.get(_COL_DATA_TYPE, ""),
            comment=cols.get(_COL_COMMENT, ""),
            devices=tuple(devices.get(key, ())),
        )
        # RowNo is what the ladder writes; RowID is accepted too because the
        # two agree for every row except the EN/ENO pins.
        entries[(str(label_id), int(row_no))] = ref
        entries.setdefault((str(label_id), int(row_id)), ref)
    return entries
