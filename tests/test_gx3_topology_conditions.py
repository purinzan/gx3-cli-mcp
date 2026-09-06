from __future__ import annotations

"""Two parallel contacts are an OR, and reporting them as an AND reverses it.

#86 and #87. `alarm-map` and `timing-chart` rebuilt a condition from the
cross-reference, which records the devices a rung mentions and not how they are
wired, then joined the contacts with " & " / " AND ".

    +--[ M100 ]--+
----|            |----( F0 )
    +--[ M101 ]--+

    reported as   M100 & M101
    actually      M100 OR M101

That is not an incomplete answer. It is the opposite of the rung, on the
commands that describe what raises an alarm and what gates a handshake.

The topology has been in `enable_logic_for_output` all along, with half a dozen
consumers. These two were rebuilding it from the wrong data.

Reading it out and then flattening it to a list would have printed the same
wrong sentence -- the first version of this fix did exactly that, and the
fixtures below are what showed it.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_alarm_map import RowIndex, row_conditions
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.review_gx3_project import LadderRow
from test_gx3_ladder_logic import manual_row
from test_gx3_shared_reach import build_xref, write_program


def project(work: Path, cases: list[tuple[dict, str]]) -> tuple[Path, Path]:
    rungs = [
        (f"_guid/{index}", generate_rung(logic, {"type": "coil", "device": device})[0])
        for index, (logic, device) in enumerate(cases)
    ]
    write_program(work / "p", rungs)
    return work / "p", build_xref(work / "p", work / "x.sqlite")


def condition(con, root: Path, device: str, wired: bool = True) -> str:
    row = con.execute(
        "select lddb, pos from xref where device=? and access='write'", (device,)
    ).fetchone()
    assert row is not None, device
    _, _, text = row_conditions(
        con, row["lddb"], row["pos"], device, RowIndex(root) if wired else None
    )
    return text


def generated_row(logic: dict, device: str) -> LadderRow:
    data, rowsize, _ = generate_rung(logic, {"type": "coil", "device": device})
    return LadderRow("test", 0, "", "", 0, rowsize, data, "", [], "exact")


class StaticRows:
    def __init__(self, row: LadderRow) -> None:
        self.row = row

    def get(self, _lddb: str, _pos: int) -> LadderRow:
        return self.row


def commentless_xref() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table xref(device text, comment text)")
    return con


def test_two_parallel_contacts_are_an_or() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [({"or": [{"device": "M100"}, {"device": "M101"}]}, "F0")])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F0")
        finally:
            con.close()
        assert "OR" in text, text
        assert "&" not in text, text


def test_a_series_contact_before_a_branch_keeps_its_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(
            work,
            [({"and": [{"device": "M200"}, {"or": [{"device": "M201"}, {"device": "M202"}]}]}, "F1")],
        )
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F1")
        finally:
            con.close()
        assert "OR" in text and "AND" in text, text
        for device in ("M200", "M201", "M202"):
            assert device in text, (device, text)


def test_a_normally_closed_contact_stays_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(
            work, [({"and": [{"device": "M300"}, {"not": {"device": "M301"}}]}, "F2")]
        )
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F2")
        finally:
            con.close()
        assert "/M301" in text, text


def test_each_output_gets_its_own_condition() -> None:
    # Two outputs in one project with different dependencies. The consumer
    # asks topology for the selected output rather than flattening the row's
    # device list into one condition.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(
            work,
            [
                ({"device": "M400"}, "F3"),
                ({"or": [{"device": "M401"}, {"device": "M402"}]}, "F4"),
            ],
        )
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            first = condition(con, root, "F3")
            second = condition(con, root, "F4")
        finally:
            con.close()
        assert "M400" in first and "M401" not in first, first
        assert "OR" in second and "M400" not in second, second


def test_alarm_self_contact_on_a_live_path_is_self_hold() -> None:
    # A real seal-in branch: start condition OR the alarm's own contact.
    row = generated_row({"or": [{"device": "M500"}, {"device": "F5"}]}, "F5")
    con = commentless_xref()
    try:
        conds, self_hold, text = row_conditions(con, "test", 0, "F5", StaticRows(row))
    finally:
        con.close()
    assert self_hold is True, (conds, text)
    assert "M500" in text and "F5" in text, text
    assert all("F5" not in item for item in conds), conds


def test_alarm_self_contact_on_a_dead_branch_is_not_self_hold() -> None:
    # The lower branch contains F5 but a driver sink above it prevents power
    # from reaching that branch. Same-row co-occurrence used to be enough to
    # call this self-hold; topology must not.
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=500:vt=nn}]}:pos=0,0}"
    blocking_coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=900:vt=nn}]}:pos=1,0}"
    wire = "e{s=wire:pos=1,1}"
    self_contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=5:vt=nn}]}:pos=2,1}"
    alarm_coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=5:vt=nn}]}:pos=3,1}"
    row = manual_row(
        f"{contact}:{blocking_coil}:{wire}:{self_contact}:{alarm_coil}",
        dim="4x2",
        header="V1:10:1:1:1:1:1:1:1:1:a:M:c:M:a:F:c:F",
        verticals="v{pos=1,1}",
    )
    con = commentless_xref()
    try:
        conds, self_hold, text = row_conditions(con, "test", 0, "F5", StaticRows(row))
    finally:
        con.close()
    assert self_hold is False, (conds, text)
    assert text == "FALSE", text


def test_without_the_rung_the_answer_says_it_is_a_contact_list() -> None:
    # The fallback is still there -- a rung that cannot be read should not stop
    # the command -- but it no longer looks like a condition.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [({"or": [{"device": "M100"}, {"device": "M101"}]}, "F0")])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F0", wired=False)
        finally:
            con.close()
        assert "wiring not read" in text, text


def test_timing_chart_reads_the_same_way() -> None:
    from gx3cli.gx3_timing_chart import RowIndex as TimingRows, same_row_conditions

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [({"or": [{"device": "M100"}, {"device": "M101"}]}, "F0")])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "select lddb, pos from xref where device='F0' and access='write'"
            ).fetchone()
            wired = same_row_conditions(
                con, row["lddb"], row["pos"], TimingRows(root), "F0"
            )
            flat = same_row_conditions(con, row["lddb"], row["pos"])
        finally:
            con.close()
        assert "OR" in wired, wired
        assert "wiring not read" in flat, flat


def main() -> int:
    test_two_parallel_contacts_are_an_or()
    test_a_series_contact_before_a_branch_keeps_its_shape()
    test_a_normally_closed_contact_stays_closed()
    test_each_output_gets_its_own_condition()
    test_alarm_self_contact_on_a_live_path_is_self_hold()
    test_alarm_self_contact_on_a_dead_branch_is_not_self_hold()
    test_without_the_rung_the_answer_says_it_is_a_contact_list()
    test_timing_chart_reads_the_same_way()
    print("topology condition checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
