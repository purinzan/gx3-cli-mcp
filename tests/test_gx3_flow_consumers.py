from __future__ import annotations

"""The value edges are of no use sitting in a table; the commands read them.

#36 asks for the edges to be consumable by graph, downstream and lint.
`downstream` came first. These are the other two.

`graph --type device-flow` walks contacts and coils: what turns this bit on. A
word device is not turned on -- a value is put into it -- so asking it about
D200 returned a graph of one node and no edges, every time. It now follows the
transfer back to the device the value came from.

`lint` already reports a word written from several rungs. Two writers are a
different thing to judge when one is a transfer from a link register and the
other is a transfer from the HMI, and the finding could not say which. It names
the source where an edge knows it, and reads exactly as before where none does.
"""

import argparse
import contextlib
import io
import sqlite3
import tempfile
import shutil
from unittest.mock import patch
from pathlib import Path

from gx3cli.gx3_dependency_flow import build_flow
from gx3cli.gx3_xref import main as xref_main


def rung(header: str, args: str) -> str:
    return (
        f"V1:9:1:1:1:1:1:1:a:M:{header}:cb{{fg=fg{{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=1:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
        f"{args}]}}:pos=1,0}}]}}}}"
    )


def mov(source: int, destination: int) -> str:
    return rung("MOV:D:D", f"d{{s=#:a={source}:vt=nn}}:d{{s=#:a={destination}:vt=nn}}")


def a_project(tmp: Path) -> Path:
    """D100 -> D200 -> D300, and D200 written from two different rungs."""
    root = tmp / "fixture"
    root.mkdir()
    con = sqlite3.connect(root / "001_LDDB.db")
    con.execute(
        "create table LadderBlocks (id text, pos real, blocktype integer, data text, "
        "rowsize integer, translated integer, ConvTarget integer)"
    )
    con.executemany(
        "insert into LadderBlocks values (?,?,?,?,?,?,?)",
        [
            ("g1", 0.0, 0, mov(100, 200), 1, 0, 0),
            ("g2", 16.0, 0, mov(200, 300), 1, 0, 0),
            ("g3", 32.0, 0, mov(900, 200), 1, 0, 0),
        ],
    )
    con.commit()
    con.close()
    return root


def build(root: Path, db: Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        assert xref_main(["--root", str(root), "--db", str(db), "build"]) == 0


def flow_for(root: Path, device: str, db: Path | None) -> dict:
    return build_flow(
        root=root, target_device=device, max_devices=50,
        include_reset=True, expand_bit_groups=False, xref_db=db,
    )


def test_graph_follows_a_value_back_to_where_it_came_from() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)

        flow = flow_for(root, "D300", db)
        value = [edge for edge in flow["edges"] if edge["kind"] == "value"]
        assert value, flow["stats"]
        assert {edge["from"] for edge in value} >= {"D200"}, value
        # And onwards: D200 was itself written from D100 and D900.
        assert {device["device"] for device in flow["devices"]} >= {"D200", "D100", "D900"}, flow[
            "devices"
        ]
        assert flow["stats"]["value_edges"] == len(value)
        assert flow["value_flow_analysis"]["state"] == "checked", flow


def test_without_a_cross_reference_it_says_nothing_rather_than_guessing() -> None:
    # This is what the answer was before: a word device has no coil driving it,
    # so the walk ends where it starts.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        flow = flow_for(root, "D300", None)
        assert flow["stats"]["value_edges"] == 0, flow["stats"]
        assert flow["value_flow_analysis"]["state"] == "not_evaluated", flow
        assert [device["device"] for device in flow["devices"]] == ["D300"], flow["devices"]


def test_flow_xref_is_checked_against_the_selected_project() -> None:
    """A foreign data_flow table must not be mixed into this project's ladder."""
    from gx3cli.gx3_flow_db import flow_xref_db

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "a").mkdir()
        (work / "b").mkdir()
        root_a = a_project(work / "a")
        root_b = a_project(work / "b")

        # Make A a distinct valid input while keeping the same project shape.
        con = sqlite3.connect(root_a / "001_LDDB.db")
        con.execute("update LadderBlocks set data=? where id='g1'", (mov(101, 200),))
        con.commit()
        con.close()

        db_b = work / "b_xref.sqlite"
        build(root_b, db_b)
        args = argparse.Namespace(xref_db=str(db_b))
        try:
            selected = flow_xref_db(args, root_a)
            flow_for(root_a, "D300", selected)
        except SystemExit as stopped:
            assert "xref db was built from a different input" in str(stopped), str(stopped)
        else:
            raise AssertionError("flow helper accepted another project's cross-reference")


def test_selected_path_is_revalidated_when_it_is_actually_read() -> None:
    from gx3cli.gx3_flow_db import flow_xref_db

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)
        selected = flow_xref_db(argparse.Namespace(xref_db=str(db)), root)
        replacement = work / "replacement.sqlite"
        shutil.copy2(db, replacement)
        with contextlib.closing(sqlite3.connect(replacement)) as con, con:
            con.execute("update meta set value='other-input' where key='input_sha256'")
        shutil.copy2(replacement, db)
        try:
            flow_for(root, "D300", selected)
        except SystemExit as exc:
            assert "different input" in str(exc), exc
        else:
            raise AssertionError("a selected path was trusted after its identity changed")


def test_value_read_uses_the_validated_sqlite_snapshot() -> None:
    from gx3cli import gx3_xref

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)
        with contextlib.closing(sqlite3.connect(db)) as con:
            assert con.execute("pragma journal_mode=WAL").fetchone()[0] == "wal"
        opened = []
        original = gx3_xref.open_xref_db

        def change_after_validation(*args, **kwargs):
            con = original(*args, **kwargs)
            opened.append(con)
            assert con.in_transaction, "validation did not pin a read snapshot"
            with contextlib.closing(sqlite3.connect(db)) as writer, writer:
                writer.execute("update data_flow set source_device='D777' where source_device='D100'")
                writer.execute("update meta set value='new-input' where key='input_sha256'")
            return con

        with patch.object(gx3_xref, "open_xref_db", side_effect=change_after_validation):
            flow = flow_for(root, "D300", db)
        sources = {edge["from"] for edge in flow["edges"] if edge["kind"] == "value"}
        assert "D100" in sources and "D777" not in sources, sources
        assert len(opened) == 1
        try:
            opened[0].execute("select 1")
        except sqlite3.ProgrammingError:
            pass
        else:
            raise AssertionError("value-flow reader kept its snapshot handle open")
        try:
            flow_for(root, "D300", db)
        except SystemExit as exc:
            assert "different input" in str(exc), exc
        else:
            raise AssertionError("the next read did not see the changed identity")


def test_missing_value_capability_is_not_a_confirmed_empty_graph() -> None:
    from gx3cli.gx3_dependency_flow import format_markdown, format_mermaid

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)
        with contextlib.closing(sqlite3.connect(db)) as con, con:
            con.execute("drop table data_flow")
        flow = flow_for(root, "D300", db)
        assert flow["value_flow_analysis"]["state"] == "not_evaluated", flow
        assert "not_evaluated" in format_mermaid(flow)
        assert "not_evaluated" in format_markdown(flow)


def test_st_and_unknown_spans_survive_value_flow_output() -> None:
    from gx3cli.gx3_dependency_flow import format_mermaid

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        with contextlib.closing(sqlite3.connect(root / "001_LDDB.db")) as con, con:
            con.execute("update LadderBlocks set data=? where id='g1'", (
                rung("BMOV:D:D:D", "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}:d{s=#:a=10:vt=nn}"),))
        with contextlib.closing(sqlite3.connect(root / "002_STDB.db")) as con, con:
            con.execute("create table Source(Pou text, Code text)")
            con.execute("insert into Source values ('MainST', 'D400 := D500;')")
        db = work / "x.sqlite"
        build(root, db)
        flow = flow_for(root, "D300", db)
        state = flow["value_flow_analysis"]
        assert state["state"] == "partial" and state["stage"] == "semantics", state
        assert len(state["constraints"]) == 2, state
        dynamic = [edge for edge in flow["edges"] if edge.get("opcode") == "BMOV"]
        assert dynamic and all(edge["span_uncertain"] for edge in dynamic), dynamic
        assert all(edge["destination_range_len"] == 0 for edge in dynamic), dynamic
        assert "range unresolved" in format_mermaid(flow)


def run_multi_writer(root: Path, db: Path) -> list[dict]:
    from gx3cli.gx3_lint import LintContext, check_multi_writer

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        ctx = LintContext(root=root, rows=[], comments={}, xref=con)
        return check_multi_writer(ctx)
    finally:
        con.close()


def test_lint_names_the_source_of_a_write_it_knows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)

        findings = [item for item in run_multi_writer(root, db) if item["device"] == "D200"]
        assert findings, "D200 is written from two rungs and was not reported"
        locations = str(findings[0]["locations"])
        assert "<-D100" in locations, locations
        assert "<-D900" in locations, locations


def test_lint_reads_as_before_when_there_are_no_edges() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)
        con = sqlite3.connect(db)
        con.execute("drop table data_flow")
        con.commit()
        con.close()

        findings = [item for item in run_multi_writer(root, db) if item["device"] == "D200"]
        assert findings, findings
        assert "<-" not in str(findings[0]["locations"]), findings[0]["locations"]


REFRESH_CSV_HEADER = (
    "object_id,network_label,unit_name,base_object_id,slot_number,unit_start_io,"
    "area_kind,direction,device_start,device_end,points_or_words,device_prefix,"
    "expected_prefix,evidence_file,evidence_offset_hex,confidence,"
    "remote_station_module_strings"
)


def a_refresh_csv(path: Path, start: str = "D900", end: str = "D900") -> Path:
    """A refresh area covering one device, so the exclusion can be checked."""
    row = f"1,net,UNIT,0,0,0,link_register,receive,{start},{end},1,D,D,x.w3pa,0x0,high,"
    path.write_text(
        REFRESH_CSV_HEADER + chr(10) + row + chr(10),
        encoding="utf-8-sig",
    )
    return path


def run_external_value_source(root: Path, db: Path, refresh_csv: Path | str) -> list[dict]:
    from gx3cli.gx3_lint import LintContext, check_external_value_source

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        ctx = LintContext(
            root=root, rows=[], comments={}, xref=con, refresh_csv=str(refresh_csv)
        )
        with contextlib.redirect_stdout(io.StringIO()):
            findings = check_external_value_source(ctx)
        return findings, ctx.states
    finally:
        con.close()


def test_a_value_from_a_word_nothing_writes_is_named_as_a_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)

        findings, _ = run_external_value_source(root, db, a_refresh_csv(work / "r.csv", "D1", "D1"))
        devices = {str(item["device"]) for item in findings}
        # D100 and D900 are moved out of; no rung writes either.
        assert {"D100", "D900"} <= devices, devices
        # D200 is written by a rung, so it is not a boundary.
        assert "D200" not in devices, devices
        assert all(item["severity"] == "info" for item in findings), findings


def test_a_device_the_network_refreshes_is_not_a_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)

        findings, _ = run_external_value_source(
            root, db, a_refresh_csv(work / "r.csv", "D900", "D900")
        )
        devices = {str(item["device"]) for item in findings}
        assert "D900" not in devices, devices
        assert "D100" in devices, devices


def test_without_refresh_areas_the_check_does_not_run() -> None:
    # Reporting a refreshed device as unexplained would be a longer list than
    # the truth, and a longer list reads as a worse project.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        db = work / "x.sqlite"
        build(root, db)

        findings, states = run_external_value_source(root, db, work / "absent.csv")
        assert findings == [], findings
        assert "external-value-source" in states, states
        assert states["external-value-source"].state == "not_evaluated", states


def main() -> int:
    test_selected_path_is_revalidated_when_it_is_actually_read()
    test_value_read_uses_the_validated_sqlite_snapshot()
    test_missing_value_capability_is_not_a_confirmed_empty_graph()
    test_st_and_unknown_spans_survive_value_flow_output()
    test_graph_follows_a_value_back_to_where_it_came_from()
    test_without_a_cross_reference_it_says_nothing_rather_than_guessing()
    test_flow_xref_is_checked_against_the_selected_project()
    test_lint_names_the_source_of_a_write_it_knows()
    test_lint_reads_as_before_when_there_are_no_edges()
    test_a_value_from_a_word_nothing_writes_is_named_as_a_boundary()
    test_a_device_the_network_refreshes_is_not_a_boundary()
    test_without_refresh_areas_the_check_does_not_run()
    print("flow consumer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
