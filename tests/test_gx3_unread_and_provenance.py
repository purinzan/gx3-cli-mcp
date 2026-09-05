from __future__ import annotations

"""Two answers that were wrong rather than short.

Found by asking two questions of the code rather than of its tests: which of
the states the vocabulary declares does anything actually construct, and does
the correction made in one traversal exist in the others.

`UNSUPPORTED` was declared, documented, required by the roadmap -- and built
nowhere. So a project holding an FBD program beside its ladder reported
"programs 1" for two of them, and every answer after that was quietly about the
ladder subset.

And the block-run fix reached the walk that `change-impact` and `downstream`
share, while `graph --type device-flow` kept its own. Asked where D900 came
from, it answered D401 and marked it terminal -- "this is where the value
originates" -- about a device a BMOV fills every scan.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_analysis_state import CHECKED, DISCOVERY, UNSUPPORTED
from gx3cli.gx3_dependency_flow import build_flow
from gx3cli.gx3_format import build_format_inventory, unsupported_programs
from test_gx3_shared_reach import BMOV, MOV_FROM_MIDDLE, build_xref, coil, write_program


def a_mixed_project(root: Path, other: str = "002_FBDDB.db") -> Path:
    write_program(root, [("_guid/a", coil("a", 1, 100))])
    con = sqlite3.connect(root / other)
    con.execute("create table Blocks (id text)")
    con.commit()
    con.close()
    return root


def test_a_program_form_this_does_not_read_is_reported_as_such() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = a_mixed_project(Path(tmp) / "mixed")
        state = unsupported_programs(build_format_inventory(root))
        assert state.state == UNSUPPORTED, state
        assert state.stage == DISCOVERY, state
        assert not state.conclusive
        assert "FBD" in state.reason, state.reason


def test_a_ladder_only_project_says_nothing_extra() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plain"
        write_program(root, [("_guid/a", coil("a", 1, 100))])
        assert unsupported_programs(build_format_inventory(root)).state == CHECKED


def test_the_counts_carry_the_warning_where_they_are_printed() -> None:
    # The point is not that the state exists; it is that a reader of
    # "programs 1" sees it.
    from gx3cli import gx3_metrics

    with tempfile.TemporaryDirectory() as tmp:
        root = a_mixed_project(Path(tmp) / "mixed")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gx3_metrics.main(["--root", str(root)])
        body = out.getvalue()
        assert "not supported" in body, body
        assert body.index("not supported") < body.index("programs"), body


def test_structured_text_counts_too() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = a_mixed_project(Path(tmp) / "mixed", other="003_STDB.db")
        state = unsupported_programs(build_format_inventory(root))
        assert state.state == UNSUPPORTED, state
        assert "ST" in state.reason, state.reason


def test_a_device_written_by_a_block_transfer_is_not_called_an_origin() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/b", BMOV), ("_guid/m", MOV_FROM_MIDDLE)])
        db = build_xref(work / "p", work / "x.sqlite")

        flow = build_flow(
            root=work / "p", target_device="D900", max_devices=50,
            include_reset=True, expand_bit_groups=False, xref_db=db,
        )
        devices = {item["device"]: item for item in flow["devices"]}

        # The BMOV writes D400..D403 and names D400. D401 is one of them.
        assert "D401" in devices, devices
        assert not devices["D401"]["terminal"], "a device a BMOV writes was called an origin"
        assert "D300" in devices, "the trace stopped before the source of the transfer"
        assert devices["D300"]["terminal"], devices["D300"]

        edges = {(edge["from"], edge["to"]) for edge in flow["edges"] if edge["kind"] == "value"}
        assert ("D300", "D401") in edges, edges
        assert ("D401", "D900") in edges, edges


def test_a_device_nothing_writes_is_still_an_origin() -> None:
    # The opposite error would be as bad: if nothing supplies a device, saying
    # so is the answer a reader needs.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/m", MOV_FROM_MIDDLE)])
        db = build_xref(work / "p", work / "x.sqlite")
        flow = build_flow(
            root=work / "p", target_device="D900", max_devices=50,
            include_reset=True, expand_bit_groups=False, xref_db=db,
        )
        devices = {item["device"]: item for item in flow["devices"]}
        assert devices["D401"]["terminal"], devices["D401"]


def main() -> int:
    test_a_program_form_this_does_not_read_is_reported_as_such()
    test_a_ladder_only_project_says_nothing_extra()
    test_the_counts_carry_the_warning_where_they_are_printed()
    test_structured_text_counts_too()
    test_a_device_written_by_a_block_transfer_is_not_called_an_origin()
    test_a_device_nothing_writes_is_still_an_origin()
    print("unread and provenance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
