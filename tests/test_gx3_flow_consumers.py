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

import contextlib
import io
import sqlite3
import tempfile
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


def test_without_a_cross_reference_it_says_nothing_rather_than_guessing() -> None:
    # This is what the answer was before: a word device has no coil driving it,
    # so the walk ends where it starts.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        flow = flow_for(root, "D300", None)
        assert flow["stats"]["value_edges"] == 0, flow["stats"]
        assert [device["device"] for device in flow["devices"]] == ["D300"], flow["devices"]


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
    test_graph_follows_a_value_back_to_where_it_came_from()
    test_without_a_cross_reference_it_says_nothing_rather_than_guessing()
    test_lint_names_the_source_of_a_write_it_knows()
    test_lint_reads_as_before_when_there_are_no_edges()
    test_a_value_from_a_word_nothing_writes_is_named_as_a_boundary()
    test_a_device_the_network_refreshes_is_not_a_boundary()
    test_without_refresh_areas_the_check_does_not_run()
    print("flow consumer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
