from __future__ import annotations

"""One specimen, several commands, and the same facts out of each.

#76's point is that the callers each grew their own traversal, so a correction
landed in one of them and not the others: value-flow edges reached
`xref downstream` and not `change-impact`, block-instruction spans reached
neither, and the exact limit reporting was fixed twice.

These are the reproductions from that issue, and a check that the two callers
now agree about the same specimen. Not that their output matches -- they answer
different questions -- but that a device one of them reaches is a device the
other reaches, because the walk underneath is the same one.
"""

import argparse
import contextlib
import io
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_analysis_state import PARTIAL, SEMANTICS
from gx3cli.gx3_change_impact import ORDER, attach_reach, collect_changes, written_devices
from gx3cli.gx3_reach import reach, successors, has_value_edges
from gx3cli.gx3_xref import downstream, main as xref_main


def rung(header: str, args: str) -> str:
    return (
        f"V1:9:1:1:1:1:1:1:a:M:{header}:cb{{fg=fg{{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=1:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
        f"{args}]}}:pos=1,0}}]}}}}"
    )


def coil(role: str, contact: int, output: int) -> str:
    return (
        f"V1:9:1:1:1:1:1:1:{role}:M:c:M:cb{{fg=fg{{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=" + role + ":as=[as{vt=Abl}]}:args=["
        f"d{{s=#:a={contact}:vt=nn}}]}}:pos=0,0}}:"
        "e{s=ce{op=cl{op=#:ct=c:as=[as{vt=Abl}]}:args=["
        f"d{{s=#:a={output}:vt=nn}}]}}:pos=1,0}}]}}}}"
    )


BMOV = rung("BMOV:D:D:K_1", "d{s=#:a=300:vt=nn}:d{s=#:a=400:vt=nn}:c{s=#:v=4}")
BMOV_EDITED = rung("BMOV:D:D:K_1", "d{s=#:a=310:vt=nn}:d{s=#:a=400:vt=nn}:c{s=#:v=4}")
MOV_FROM_MIDDLE = rung("MOV:D:D", "d{s=#:a=401:vt=nn}:d{s=#:a=900:vt=nn}")


def write_program(root: Path, rungs: list[tuple[str, str]], stride: int = 16) -> None:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "001_LDDB.db")
    con.execute(
        "create table LadderBlocks (id text, pos real, blocktype integer, data text, "
        "rowsize integer, translated integer, ConvTarget integer)"
    )
    con.executemany(
        "insert into LadderBlocks values (?,?,?,?,?,?,?)",
        [(guid, float(i * stride), 0, data, 1, 0, 0) for i, (guid, data) in enumerate(rungs)],
    )
    con.commit()
    con.close()


def build_xref(root: Path, db: Path) -> Path:
    with contextlib.redirect_stdout(io.StringIO()):
        assert xref_main(["--root", str(root), "--db", str(db), "build"]) == 0
    return db


def test_a_block_write_is_followed_through_the_middle_of_its_run() -> None:
    """#76: BMOV D300 D400 K4 writes D400..D403, and D401 leads on to D900.

    The walk started from D400 alone, because that is the only device the
    instruction names. Everything reading the rest of the run was invisible,
    and the run reported "checked".
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "old", [("_guid/b", BMOV), ("_guid/m", MOV_FROM_MIDDLE)])
        write_program(work / "new", [("_guid/b", BMOV_EDITED), ("_guid/m", MOV_FROM_MIDDLE)])
        db = build_xref(work / "new", work / "x.sqlite")

        devices, _, _ = written_devices(BMOV_EDITED)
        assert devices == ["D400", "D401", "D402", "D403"], devices

        changes = collect_changes(work / "old", work / "new", include_comments=False)
        attach_reach(changes, db, max_depth=3, max_nodes=50, root=work / "new")
        reached = {item["device"] for change in changes for item in change.reaches}
        assert "D900" in reached, reached
        # And the evidence says which device carried it there.
        step = next(
            item
            for change in changes
            for item in change.reaches
            if item["device"] == "D900"
        )
        assert step["from"] == "D401", step


def test_a_reordered_pair_of_rungs_is_a_change() -> None:
    """#76: two rungs writing one coil, swapped. Contents identical.

    The comparison keyed on GUID and rung data, so a swap produced no
    difference at all and the run printed "no change that can alter
    behaviour" -- about two rungs driving the same output in the other order.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        first = ("_guid/one", coil("a", 1, 100))
        second = ("_guid/two", coil("a", 2, 100))
        write_program(work / "old", [first, second])
        write_program(work / "new", [second, first])

        changes = collect_changes(work / "old", work / "new", include_comments=False)
        assert changes, "a swap of two rungs was not seen at all"
        assert {change.kind for change in changes} == {ORDER}, [c.as_dict() for c in changes]
        assert all("M100" in change.writes for change in changes), changes


def test_renumbering_positions_is_not_an_execution_change() -> None:
    # Positions are rewritten whenever anything above them is edited. Calling
    # that an execution change would put a finding on nearly every diff, and a
    # finding that is always there is not read.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        rungs = [("_guid/one", coil("a", 1, 100)), ("_guid/two", coil("a", 2, 100))]
        write_program(work / "old", rungs, stride=16)
        write_program(work / "new", rungs, stride=64)
        assert collect_changes(work / "old", work / "new", include_comments=False) == []


def test_a_cross_reference_of_another_project_is_refused() -> None:
    """#76: it answered with silence, exit code 0, "checked", nothing reached."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "old", [("_guid/one", coil("a", 1, 100))])
        write_program(work / "new", [("_guid/one", coil("b", 1, 100))])
        write_program(work / "unrelated", [("_guid/x", coil("a", 700, 800))])
        stranger = build_xref(work / "unrelated", work / "stranger.sqlite")

        changes = collect_changes(work / "old", work / "new", include_comments=False)
        try:
            attach_reach(changes, stranger, max_depth=2, max_nodes=50, root=work / "new")
        except SystemExit as exit_error:
            assert "different input" in str(exit_error), str(exit_error)
        else:
            raise AssertionError("a cross-reference of another project was accepted")

        # The right one is still accepted.
        own = build_xref(work / "new", work / "own.sqlite")
        state = attach_reach(changes, own, max_depth=2, max_nodes=50, root=work / "new")
        assert state.state in {"checked", "truncated"}, state


def test_both_callers_walk_the_same_graph() -> None:
    # They answer different questions and print differently. What has to match
    # is the set of devices reachable from one device, because that is one
    # question with one answer.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(
            work / "p",
            [
                ("_guid/a", coil("a", 1, 100)),
                ("_guid/b", coil("a", 100, 200)),
                ("_guid/c", coil("a", 200, 300)),
            ],
        )
        db = build_xref(work / "p", work / "x.sqlite")

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            walked = {step.device for step in reach(con, "M100", 5, 100).steps}
        finally:
            con.close()

        args = argparse.Namespace(
            db=str(db), root=str(work / "p"), device="M100",
            max_depth=5, max_nodes=100, max_children=100, strict_bit=False,
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            downstream(args)
        printed = out.getvalue()

        assert walked, "the shared walk found nothing to compare"
        for device in walked:
            assert device in printed, (device, printed)


def test_a_transfer_outranks_sharing_a_rung_as_the_basis() -> None:
    # Both are true of a transfer, and "they share a rung" says much less. When
    # the two lists were merged by order, whichever row came back first
    # decided, and every entry read "same-rung".
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/m", rung("MOV:D:D", "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}"))])
        db = build_xref(work / "p", work / "x.sqlite")

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            assert has_value_edges(con)
            bases = {
                str(row["device"]): basis
                for row, basis in successors(con, "D100", True)
            }
        finally:
            con.close()
        assert bases.get("D200") == "via MOV", bases


DYNAMIC_BMOV = rung("BMOV:D:D:D", "d{s=#:a=300:vt=nn}:d{s=#:a=400:vt=nn}:d{s=#:a=10:vt=nn}")
DYNAMIC_BMOV_EDITED = rung("BMOV:D:D:D", "d{s=#:a=310:vt=nn}:d{s=#:a=400:vt=nn}:d{s=#:a=10:vt=nn}")


def test_a_run_whose_length_lives_in_a_device_is_not_treated_as_settled() -> None:
    """`BMOV D300 D400 D10` writes as many words as D10 holds when it runs.

    The instruction was read correctly, so this is not a decoding gap; how far
    the run reaches is a value the running program has. Reported as "checked"
    it claimed the write was D400 and nothing else, which is true only if D10
    happens to hold 1.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "old", [("_guid/b", DYNAMIC_BMOV)])
        write_program(work / "new", [("_guid/b", DYNAMIC_BMOV_EDITED)])
        db = build_xref(work / "new", work / "x.sqlite")

        devices, unreadable, unsettled = written_devices(DYNAMIC_BMOV_EDITED)
        assert devices == ["D400"], devices
        assert not unreadable, "the instruction reads fine; only its extent is open"
        assert unsettled, "a run of unknown length was recorded as settled"

        changes = collect_changes(work / "old", work / "new", include_comments=False)
        state = attach_reach(changes, db, max_depth=3, max_nodes=50, root=work / "new")
        assert state.state == PARTIAL, state
        assert state.stage == SEMANTICS, state
        assert not state.conclusive
        assert "held in a device" in state.reason, state.reason


def test_a_constant_run_stays_settled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "old", [("_guid/b", BMOV)])
        write_program(work / "new", [("_guid/b", BMOV_EDITED)])
        db = build_xref(work / "new", work / "x.sqlite")
        changes = collect_changes(work / "old", work / "new", include_comments=False)
        state = attach_reach(changes, db, max_depth=3, max_nodes=50, root=work / "new")
        assert state.stage != SEMANTICS, state


def test_the_walk_matches_a_device_inside_a_recorded_run() -> None:
    """Asking about the middle of a run finds the rung that covers it.

    A run is stored once, under its first device, with its length beside it, so
    matching on the name alone answers "nothing uses D301" about a device a
    rung reads every scan.

    The row here is written by hand because the builder does not currently
    record a run on the reading side: `BMOV (s) (d) (n)` copies a run from (s),
    and `FMOV (s) (d) (n)` fills from a single (s), and the manual data in this
    repository spells both as ("(s)", "(d)", "(n)"). Which of the two an
    instruction is cannot be told from what we have, so it is not guessed. The
    walk is ready for those rows; nothing produces them yet.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/m", MOV_FROM_MIDDLE)])
        db = build_xref(work / "p", work / "x.sqlite")

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            con.execute(
                "insert into xref(device, device_type, number, range_len, access, role,"
                " opcode, arg_index, const_args, detail, access_basis, lddb, pos, pou,"
                " step, title, comment, parse_status)"
                " values ('D300','D',300,4,'read','','BLOCK',0,'','','manual',"
                " '001_LDDB.db', 999, '001', 999, '', '', 'exact')"
            )
            con.execute(
                "insert into xref(device, device_type, number, range_len, access, role,"
                " opcode, arg_index, const_args, detail, access_basis, lddb, pos, pou,"
                " step, title, comment, parse_status)"
                " values ('D888','D',888,1,'write','','BLOCK',1,'','','manual',"
                " '001_LDDB.db', 999, '001', 999, '', 'downstream of the run', 'exact')"
            )
            con.commit()

            named = {step.device for step in reach(con, "D300", 2, 50).steps}
            middle = {step.device for step in reach(con, "D301", 2, 50).steps}
            past_end = {step.device for step in reach(con, "D304", 2, 50).steps}
        finally:
            con.close()

        assert "D888" in named, named
        assert "D888" in middle, "a device inside the run was not matched"
        assert "D888" not in past_end, "a device past the end of the run was matched"


def main() -> int:
    test_a_block_write_is_followed_through_the_middle_of_its_run()
    test_a_reordered_pair_of_rungs_is_a_change()
    test_renumbering_positions_is_not_an_execution_change()
    test_a_cross_reference_of_another_project_is_refused()
    test_both_callers_walk_the_same_graph()
    test_a_transfer_outranks_sharing_a_rung_as_the_basis()
    test_a_run_whose_length_lives_in_a_device_is_not_treated_as_settled()
    test_a_constant_run_stays_settled()
    test_the_walk_matches_a_device_inside_a_recorded_run()
    print("shared reach checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
