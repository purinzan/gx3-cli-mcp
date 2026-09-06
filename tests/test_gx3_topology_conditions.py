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


# #139: constants proved elsewhere in the project also simplify normal coil
# tracing. These regressions are about which upstream conditions still matter
# to the ON question, not about declaring live PLC state.
def _contact(device: str, role: str = "a", position: str = "0,0") -> dict:
    return {
        "op": "contact",
        "role": role,
        "state": "ON" if role == "a" else "OFF",
        "device": device,
        "raw_device": device,
        "device_type": device.rstrip("0123456789"),
        "position": position,
    }


def _fact(device: str, value: bool):
    from gx3cli.gx3_dead_logic import ConstantFact

    return ConstantFact(
        device=device,
        value=value,
        where="SYNTH st1",
        chain=(f"{device}={'ON' if value else 'OFF'}",),
        roots=("synthetic",),
        depth=1,
    )


def test_constant_false_in_and_prunes_the_whole_upstream_branch() -> None:
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    logic = {
        "op": "and",
        "args": [_contact("X0", position="0,0"), _contact("M100", position="1,0"), _contact("M200", "b", "2,0")],
    }
    result = simplify_logic_for_trace(logic, {"M100": _fact("M100", False)})
    assert result.logic_text == "FALSE", result
    assert condition_refs_from_logic(result.logic) == []
    assert {ref["device"] for ref in result.pruned_conditions} == {"X0", "M100", "M200"}


def test_false_or_branch_is_removed_but_other_on_path_is_still_traced() -> None:
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic, logic_to_text
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    logic = {
        "op": "or",
        "args": [
            {"op": "and", "args": [_contact("M100", position="0,0"), _contact("X0", position="1,0")]},
            {"op": "and", "args": [_contact("X1", position="0,1"), _contact("M300", position="1,1")]},
        ],
    }
    result = simplify_logic_for_trace(logic, {"M100": _fact("M100", False)})
    refs = condition_refs_from_logic(result.logic)
    assert {ref["device"] for ref in refs} == {"X1", "M300"}, (logic_to_text(result.logic), refs)
    assert "X0" not in {ref["device"] for ref in refs}


def test_true_or_branch_short_circuits_the_other_upstream_conditions() -> None:
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    logic = {"op": "or", "args": [_contact("M200"), _contact("M400", position="1,0")]}
    result = simplify_logic_for_trace(logic, {"M200": _fact("M200", True)})
    assert result.logic_text == "TRUE"
    assert condition_refs_from_logic(result.logic) == []


def test_b_contact_inverts_the_proven_coil_state_before_pruning() -> None:
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    assert simplify_logic_for_trace(_contact("M200", "b"), {"M200": _fact("M200", True)}).logic_text == "FALSE"
    assert simplify_logic_for_trace(_contact("M100", "b"), {"M100": _fact("M100", False)}).logic_text == "TRUE"


def test_public_trace_row_filter_keeps_only_conditions_left_after_simplification() -> None:
    from gx3cli.trace_gx3_device_dependencies import _filter_row_conditions

    logic = {
        "op": "or",
        "args": [
            {"op": "and", "args": [_contact("M100"), _contact("X0", position="1,0")]},
            _contact("X1", position="0,1"),
        ],
    }
    row = {
        "enable_logic": logic,
        "enable_logic_text": "raw",
        "conditions": [
            {"device": "M100", "role": "a", "required_state": "ON"},
            {"device": "X0", "role": "a", "required_state": "ON"},
            {"device": "X1", "role": "a", "required_state": "ON"},
        ],
    }
    removed = _filter_row_conditions(row, {"M100": _fact("M100", False)})
    assert removed == 2, row
    assert [condition["device"] for condition in row["conditions"]] == ["X1"], row
    assert row["enable_logic_text"] == "[X1]", row
    assert row["raw_enable_logic_text"] == "raw"


def test_public_trace_dispatch_prunes_refs_before_canonical_bfs_queue() -> None:
    from gx3cli import gx3_trace_state as base
    from gx3cli.trace_gx3_device_dependencies import (
        _ACTIVE_CONSTANT_FACTS,
        _ACTIVE_PRUNE_STATS,
        _condition_refs_dispatch,
    )

    logic = {
        "op": "or",
        "args": [
            {"op": "and", "args": [_contact("M100"), _contact("X0", position="1,0")]},
            _contact("X1", position="0,1"),
        ],
    }
    assert base.condition_refs_from_logic is _condition_refs_dispatch
    stats: dict[str, int] = {}
    facts_token = _ACTIVE_CONSTANT_FACTS.set({"M100": _fact("M100", False)})
    stats_token = _ACTIVE_PRUNE_STATS.set(stats)
    try:
        refs = base.condition_refs_from_logic(logic)
    finally:
        _ACTIVE_PRUNE_STATS.reset(stats_token)
        _ACTIVE_CONSTANT_FACTS.reset(facts_token)

    assert [ref["device"] for ref in refs] == ["X1"], refs
    assert stats["raw_refs"] == 3, stats
    assert stats["kept_refs"] == 1, stats
    assert stats["raw_refs"] - stats["kept_refs"] == 2, stats


def test_postfilter_row_key_includes_the_driven_device() -> None:
    from gx3cli.trace_gx3_device_dependencies import _row_device_key

    y0_row = {"row_id": "P1:10", "device": "Y0"}
    y1_row = {"row_id": "P1:10", "device": "Y1"}
    assert _row_device_key(y0_row) != _row_device_key(y1_row)
    assert _row_device_key({"row_id": "P1:10", "from_device": "Y0"}) == _row_device_key(y0_row)
    assert _row_device_key({"row_id": "P1:10", "from_device": "Y1"}) == _row_device_key(y1_row)


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} topology condition checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
