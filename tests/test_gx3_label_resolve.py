from __future__ import annotations

"""A label-based program has to come out with its labels named.

A program written with labels spells its contacts and coils as
"_lid/<LabelID>/<row>" rather than as a device. The decoder recognised those
and counted them, so the rung structure was right, but never looked up what
they named -- every occurrence arrived with no identity at all.

On a project written that way the cross-reference came out empty, and with it
every tool that reads it: where-used, trace-device, interlock, dead-logic,
every lint check. ladder-print drew the rung correctly with "?" at each
contact. And the parse reported itself as exact throughout, so nothing said
anything was missing.

LabelData.db sits beside the program and holds the answer.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_label_resolve import (
    EMPTY,
    load_label_resolver,
    split_label_token,
)

LABEL_ID = "9162445254180170159"

# class, name, comment, assigned devices
_ROWS = {
    1: ("VAR_INPUT", "IN_interlock", "Interlock Input", ()),
    2: ("VAR_INPUT", "IN_Start", "Start Input", ()),
    3: ("VAR_INPUT", "IN_Stop", "Stop Input", ()),
    7: ("VAR", "Start_Latch", "", ()),
    8: ("VAR", "Fault_Latch", "", ()),
}
_GLOBAL_ID = "2826966274964139082"


def _write_label_db(root: Path) -> None:
    """The tables LabelData.db actually uses, with the columns we read."""
    con = sqlite3.connect(root / "LabelData.db")
    con.execute("create table LabelTbl (LabelID text, LabelTypeID integer)")
    con.execute("create table RowTbl (LabelID text, RowID integer, RowNo integer)")
    con.execute(
        "create table ColumnDataTbl (LabelID text, RowID integer, ColumnID integer, ColumnStrValue text)"
    )
    con.execute("create table DeviceAssignTbl (LabelID text, RowID integer, MELSECDevice text)")
    for row_no, (cls, name, comment, _devices) in _ROWS.items():
        con.execute("insert into RowTbl values (?, ?, ?)", (LABEL_ID, row_no, row_no))
        for column_id, value in ((1, cls), (2, name), (15, comment)):
            con.execute(
                "insert into ColumnDataTbl values (?, ?, ?, ?)", (LABEL_ID, row_no, column_id, value)
            )
    # A global label assigned to real devices. One row of a structure covers
    # several, which is why the assignment is recorded beside the label rather
    # than replacing it.
    con.execute("insert into RowTbl values (?, ?, ?)", (_GLOBAL_ID, 1, 1))
    con.execute("insert into ColumnDataTbl values (?, ?, ?, ?)", (_GLOBAL_ID, 1, 1, "VAR_GLOBAL"))
    con.execute("insert into ColumnDataTbl values (?, ?, ?, ?)", (_GLOBAL_ID, 1, 2, "Motor_1_Data"))
    for device in ("X0", "X1", "Y0"):
        con.execute("insert into DeviceAssignTbl values (?, ?, ?)", (_GLOBAL_ID, 1, device))
    con.commit()
    con.close()


def _rung(*refs: tuple[str, int]) -> str:
    """A rung of label contacts and one coil, in the intermediate spelling."""
    header = ":".join(f"{role}:_lid/{LABEL_ID}/{row}" for role, row in refs)
    elements = ":".join(
        "e{s=ce{op=%s{op=#:ct=a:as=[as{vt=Abl}]}:args=[l{id=#}]}:pos=%d,0}"
        % ("cl" if role == "c" else "ct", index)
        for index, (role, _row) in enumerate(refs)
    )
    return f"V1:{len(refs) * 2}:1:26:{header}:cb{{fg=fg{{dim={len(refs)}x1:es=[{elements}]}}}}"


def test_token_is_split_into_table_and_row() -> None:
    assert split_label_token(f"_lid/{LABEL_ID}/2") == (LABEL_ID, 2)
    assert split_label_token("D200") is None
    assert split_label_token("_lid/only-two") is None


def test_labels_resolve_to_their_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_label_db(root)
        labels = load_label_resolver(root)

        ref = labels.resolve_token(f"_lid/{LABEL_ID}/2")
        assert ref is not None
        assert ref.name == "IN_Start"
        assert ref.label_class == "VAR_INPUT"
        assert ref.comment == "Start Input"

        # A row the table does not have resolves to nothing, rather than to
        # something plausible.
        assert labels.resolve_token(f"_lid/{LABEL_ID}/999") is None
        assert labels.resolve_token("_lid/0000/1") is None


def test_assigned_devices_are_recorded_beside_the_label() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_label_db(root)
        ref = load_label_resolver(root).resolve_token(f"_lid/{_GLOBAL_ID}/1")
        assert ref is not None
        assert ref.name == "Motor_1_Data"
        assert ref.devices == ("X0", "X1", "Y0")
        assert "X0" in ref.detail


def test_a_rung_of_labels_decodes_to_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_label_db(root)
        labels = load_label_resolver(root)

        data = _rung(("a", 2), ("b", 3), ("c", 7))
        ops, status = parse_row_occurrences(data, labels)
        found = [(role, occ.device, occ.access) for role, _op, occs, _c in ops for occ in occs]

        assert status == "exact"
        assert found == [
            ("a", "IN_Start", "read"),
            ("b", "IN_Stop", "read"),
            ("c", "Start_Latch", "write"),
        ]


def test_without_the_label_table_the_reference_is_kept_not_dropped() -> None:
    # No LabelData.db: the occurrence still says something is there and what
    # it referred to, so an empty cross-reference cannot be mistaken for a
    # rung with nothing in it.
    data = _rung(("a", 2), ("c", 7))
    ops, _status = parse_row_occurrences(data, EMPTY)
    found = [(occ.device, occ.detail) for _r, _o, occs, _c in ops for occ in occs]
    assert [name for name, _detail in found] == [f"_lid/{LABEL_ID}/2", f"_lid/{LABEL_ID}/7"]
    assert all(detail == "label (unresolved)" for _name, detail in found)


def test_a_project_with_no_label_table_loads_an_empty_resolver() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        labels = load_label_resolver(Path(tmp))
        assert not labels
        assert labels.resolve_token(f"_lid/{LABEL_ID}/2") is None


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} label-resolution checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
