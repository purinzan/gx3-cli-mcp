from __future__ import annotations

"""A change, and what it reaches -- separated from a change that reaches nothing.

`semantic-diff` says which rungs differ. The question behind that is which
outputs and which alarms can behave differently, and answering it by hand meant
taking each changed rung, reading what it writes, and running `downstream` once
per device.

What is pinned here is mostly about restraint.

A comment is not a behaviour change. Neither is a canvas resize. Those are
reported as what they are, with no impact list attached, because an impact list
next to a comment edit teaches the reader to ignore impact lists.

The reach is candidates, not consequences, and the output says so in those
words. Every device in it is there because the saved file connects it, not
because that path runs.

A changed rung the decoder could not fully read makes the reach incomplete, and
the run says so rather than presenting a short list as the whole one.
"""

import contextlib
import io
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_analysis_state import CHECKED, PARTIAL, REACH, TRUNCATED, AnalysisState
from gx3cli.gx3_change_impact import (
    ADDED,
    COMMENT_ONLY,
    LAYOUT_ONLY,
    LOGIC,
    attach_reach,
    collect_changes,
    render,
    written_devices,
)
from gx3cli.gx3_xref import main as xref_main


def coil(contact_role: str, contact: int, output: int) -> str:
    """A rung: one contact driving one coil."""
    return (
        f"V1:9:1:1:1:1:1:1:{contact_role}:M:c:M:cb{{fg=fg{{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=" + contact_role + ":as=[as{vt=Abl}]}:args=["
        f"d{{s=#:a={contact}:vt=nn}}]}}:pos=0,0}}:"
        "e{s=ce{op=cl{op=#:ct=c:as=[as{vt=Abl}]}:args=["
        f"d{{s=#:a={output}:vt=nn}}]}}:pos=1,0}}]}}}}"
    )


def write_program(root: Path, rungs: list[tuple[str, str]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / "001_LDDB.db")
    con.execute(
        "create table LadderBlocks (id text, pos real, blocktype integer, data text, "
        "rowsize integer, translated integer, ConvTarget integer)"
    )
    con.executemany(
        "insert into LadderBlocks values (?,?,?,?,?,?,?)",
        [(guid, float(i * 16), 0, data, 1, 0, 0) for i, (guid, data) in enumerate(rungs)],
    )
    con.commit()
    con.close()


def two_versions(work: Path, new_rungs: list[tuple[str, str]]) -> tuple[Path, Path]:
    """M1 -> M100 -> M200, with the new version differing as given."""
    old = [
        ("_guid/one", coil("a", 1, 100)),
        ("_guid/two", coil("a", 100, 200)),
    ]
    write_program(work / "old", old)
    write_program(work / "new", new_rungs)
    return work / "old", work / "new"


def xref_for(root: Path, db: Path) -> Path:
    with contextlib.redirect_stdout(io.StringIO()):
        assert xref_main(["--root", str(root), "--db", str(db), "build"]) == 0
    return db


def test_a_changed_contact_reaches_what_the_rung_drives() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("b", 1, 100)),  # a-contact became b-contact
                ("_guid/two", coil("a", 100, 200)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        logic = [change for change in changes if change.kind == LOGIC]
        assert len(logic) == 1, [c.as_dict() for c in changes]
        assert logic[0].writes == ["M100"], logic[0].writes

        state = attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=2, max_nodes=50)
        assert state.state == CHECKED, state
        reached = {item["device"] for item in logic[0].reaches}
        # M100 is read by the second rung, which drives M200: the change
        # reaches it, and a reader who stopped at "M100 changed" would not know.
        assert "M200" in reached, reached


def test_an_added_rung_is_followed_too() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("a", 1, 100)),
                ("_guid/two", coil("a", 100, 200)),
                ("_guid/three", coil("a", 200, 300)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        added = [change for change in changes if change.kind == ADDED]
        assert len(added) == 1, [c.as_dict() for c in changes]
        assert added[0].writes == ["M300"], added[0].writes


def test_a_canvas_resize_is_layout_and_carries_no_impact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("a", 1, 100).replace("dim=4x1", "dim=8x1")),
                ("_guid/two", coil("a", 100, 200)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        kinds = {change.kind for change in changes}
        assert kinds == {LAYOUT_ONLY}, [c.as_dict() for c in changes]

        attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=2, max_nodes=50)
        assert all(change.reaches == [] for change in changes), changes
        body = "\n".join(render(changes, AnalysisState(CHECKED), 10))
        assert "No change that can alter behaviour" in body, body


def test_a_moved_element_is_not_called_layout() -> None:
    # Element positions carry the wiring, so moving one is treated as a logic
    # change rather than assumed harmless. Being conservative here is the whole
    # value of the distinction: "layout only" has to mean it.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("a", 1, 100).replace("pos=1,0", "pos=2,0")),
                ("_guid/two", coil("a", 100, 200)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        assert {change.kind for change in changes} == {LOGIC}, [c.as_dict() for c in changes]


def test_a_comment_edit_alone_reaches_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work, [("_guid/one", coil("a", 1, 100)), ("_guid/two", coil("a", 100, 200))]
        )
        for root, text in ((old, "before"), (new, "after")):
            con = sqlite3.connect(root / "001_DC.db")
            con.execute("create table DEVICE_DATA (SEQ integer, DevCode integer, DevNoLow integer)")
            con.execute(
                "create table COMMENT_DATA (DeviceSEQ integer, CmtNo integer, "
                "CmtData text, DelFlag integer)"
            )
            con.execute("insert into DEVICE_DATA values (1, 1, 100)")  # M100
            con.execute("insert into COMMENT_DATA values (1, 5, ?, 0)", (text,))
            con.commit()
            con.close()

        changes = collect_changes(old, new, include_comments=True)
        assert {change.kind for change in changes} == {COMMENT_ONLY}, [c.as_dict() for c in changes]
        state = attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=2, max_nodes=50)
        assert state.state == CHECKED, state
        assert all(change.reaches == [] for change in changes)


def test_an_unreadable_rung_makes_the_reach_incomplete_and_says_so() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                # A header naming operands the elements do not supply: the
                # decoder reads the rung, and knows it did not read all of it.
                ("_guid/one", coil("a", 1, 100).replace(":c:M:cb{", ":c:M:MOV:D:D:cb{")),
                ("_guid/two", coil("a", 100, 200)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        logic = [change for change in changes if change.kind == LOGIC]
        assert logic and logic[0].unreadable, [c.as_dict() for c in changes]

        state = attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=2, max_nodes=50)
        assert state.state == PARTIAL, state
        body = "\n".join(render(changes, state, 10))
        assert "could not be read" in body, body


def test_the_output_calls_the_reach_candidates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [("_guid/one", coil("b", 1, 100)), ("_guid/two", coil("a", 100, 200))],
        )
        changes = collect_changes(old, new, include_comments=False)
        state = attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=2, max_nodes=50)
        body = "\n".join(render(changes, state, 10))
        assert "static candidates" in body, body
        assert "not decided here" in body, body


def test_written_devices_follows_the_decoder_on_what_was_read() -> None:
    # The flag is the decoder's own parse status, not a guess of this module's.
    # Text with nothing in it to decode yields no writes and no complaint; the
    # case that matters -- a rung whose header names operands its elements do
    # not supply -- is covered above, and is what a real gap looks like.
    devices, unreadable, _ = written_devices("not a rung at all")
    assert devices == [], devices
    assert not unreadable, "nothing to decode is not the same as failing to decode"


def test_a_reach_that_stopped_at_a_limit_is_not_called_checked() -> None:
    """The failure this exists for: a shorter answer that reads as a complete one.

    M100 -> M200 -> M300, walked at depth 1. M300 is missing from the reach and
    the run reported "checked within the supported range", which is the one
    thing it must not say: raising the depth changes the answer, and nothing in
    the output suggested trying.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("b", 1, 100)),
                ("_guid/two", coil("a", 100, 200)),
                ("_guid/three", coil("a", 200, 300)),
            ],
        )
        db = xref_for(new, work / "x.sqlite")

        shallow = collect_changes(old, new, include_comments=False)
        state = attach_reach(shallow, db, max_depth=1, max_nodes=50)
        logic = [change for change in shallow if change.kind == LOGIC][0]
        assert "M300" not in {item["device"] for item in logic.reaches}
        assert state.state == TRUNCATED, state
        assert state.stage == REACH, state
        assert not state.conclusive
        assert "max-depth" in state.reason, state.reason
        assert logic.truncated

        deep = collect_changes(old, new, include_comments=False)
        deep_state = attach_reach(deep, db, max_depth=4, max_nodes=50)
        deep_logic = [change for change in deep if change.kind == LOGIC][0]
        assert "M300" in {item["device"] for item in deep_logic.reaches}
        assert deep_state.state == CHECKED, deep_state


def test_a_limit_that_hid_nothing_is_not_reported() -> None:
    """Exactly at the limit, with everything found, is a complete answer.

    The first fix for the opposite bug over-corrected: any walk that touched
    the depth number was called truncated, so M100 -> M200 -> M300 at depth 2 --
    which finds every device there is -- claimed to be missing something. A
    reader who sees "truncated" on complete answers stops reading the word,
    which costs exactly what the original silence cost.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("b", 1, 100)),
                ("_guid/two", coil("a", 100, 200)),
                ("_guid/three", coil("a", 200, 300)),
            ],
        )
        db = xref_for(new, work / "x.sqlite")

        # Two devices are reachable, and the walk is two deep.
        for depth in (2, 3, 4):
            changes = collect_changes(old, new, include_comments=False)
            state = attach_reach(changes, db, max_depth=depth, max_nodes=50)
            reached = {
                item["device"]
                for change in changes
                for item in change.reaches
            }
            assert reached == {"M200", "M300"}, (depth, reached)
            assert state.state == CHECKED, (depth, state)

        # And the node limit, exactly on the number that fits.
        for nodes in (2, 3):
            changes = collect_changes(old, new, include_comments=False)
            state = attach_reach(changes, db, max_depth=9, max_nodes=nodes)
            assert state.state == CHECKED, (nodes, state)

        changes = collect_changes(old, new, include_comments=False)
        state = attach_reach(changes, db, max_depth=9, max_nodes=1)
        assert state.state == TRUNCATED, state


def test_a_node_limit_is_reported_the_same_way() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("b", 1, 100)),
                ("_guid/two", coil("a", 100, 200)),
                ("_guid/three", coil("a", 200, 300)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        state = attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=9, max_nodes=1)
        assert state.state == TRUNCATED, state
        assert "max-nodes" in state.reason, state.reason


def test_the_page_says_which_change_was_cut_short() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        old, new = two_versions(
            work,
            [
                ("_guid/one", coil("b", 1, 100)),
                ("_guid/two", coil("a", 100, 200)),
                ("_guid/three", coil("a", 200, 300)),
            ],
        )
        changes = collect_changes(old, new, include_comments=False)
        state = attach_reach(changes, xref_for(new, work / "x.sqlite"), max_depth=1, max_nodes=50)
        body = "\n".join(render(changes, state, 10))
        assert "stopped at a limit" in body, body


def main() -> int:
    test_a_changed_contact_reaches_what_the_rung_drives()
    test_an_added_rung_is_followed_too()
    test_a_canvas_resize_is_layout_and_carries_no_impact()
    test_a_moved_element_is_not_called_layout()
    test_a_comment_edit_alone_reaches_nothing()
    test_an_unreadable_rung_makes_the_reach_incomplete_and_says_so()
    test_the_output_calls_the_reach_candidates()
    test_written_devices_follows_the_decoder_on_what_was_read()
    test_a_reach_that_stopped_at_a_limit_is_not_called_checked()
    test_a_limit_that_hid_nothing_is_not_reported()
    test_a_node_limit_is_reported_the_same_way()
    test_the_page_says_which_change_was_cut_short()
    print("change impact checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
