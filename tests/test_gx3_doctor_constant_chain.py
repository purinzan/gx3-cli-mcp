from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_audit import build_health_report, collect_constant_chains
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_lint import LintContext
from gx3cli.review_gx3_project import LadderRow


XREF_SCHEMA = """
create table xref (
    id integer primary key autoincrement,
    device text, device_type text, number integer,
    range_len integer not null default 1,
    access text, role text, opcode text, detail text,
    lddb text, pos integer, pou text, step integer,
    comment text
)
"""


def row(logic: dict, output: str, pos: int, lddb: str) -> LadderRow:
    data, rowsize, _ = generate_rung(logic, {"type": "coil", "device": output})
    return LadderRow(lddb, pos, f"b{pos}", "", 0, rowsize, data, "", [], "exact")


def insert_xref(
    con: sqlite3.Connection,
    device: str,
    device_type: str,
    number: int,
    access: str,
    role: str,
    lddb: str,
    pos: int,
    pou: str,
    step: int,
) -> None:
    con.execute(
        """
        insert into xref(device, device_type, number, range_len, access, role, opcode,
                         detail, lddb, pos, pou, step, comment)
        values (?, ?, ?, 1, ?, ?, ?, '', ?, ?, ?, ?, '')
        """,
        (device, device_type, number, access, role, role, lddb, pos, pou, step),
    )


def make_lite(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.execute(
        "create table external_sources(device text primary key, source_kind text, semantic_group text)"
    )
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def test_physical_y_constant_chain_is_promoted_to_doctor_top_risk() -> None:
    """SM401 -> M100 OFF -> M100 A false -> Y0 ALWAYS_OFF -> Doctor HIGH."""
    with tempfile.TemporaryDirectory(prefix="gx3_doctor_chain_") as tmp:
        work = Path(tmp)
        index_path = work / "fixture.sqlite"
        lite = make_lite(index_path)
        xref = sqlite3.connect(":memory:")
        xref.executescript(XREF_SCHEMA)
        xref.row_factory = sqlite3.Row
        rows = [
            row({"device": "SM401"}, "M100", 10, "P1_LDDB.db"),
            row({"device": "M100"}, "Y0", 20, "P2_LDDB.db"),
        ]
        insert_xref(xref, "SM401", "SM", 401, "read", "a", "P1_LDDB.db", 10, "P1", 10)
        insert_xref(xref, "M100", "M", 100, "write", "c", "P1_LDDB.db", 10, "P1", 10)
        insert_xref(xref, "M100", "M", 100, "read", "a", "P2_LDDB.db", 20, "P2", 20)
        insert_xref(xref, "Y0", "Y", 0, "write", "c", "P2_LDDB.db", 20, "P2", 20)
        xref.commit()

        ctx = LintContext(root=work, rows=rows, comments={}, xref=xref, lite=lite)
        findings = collect_constant_chains(ctx, index_db=index_path)
        assert len(findings) == 1, findings
        finding = findings[0]
        assert finding["check"] == "constant-chain"
        assert finding["severity"] == "high"
        assert finding["device"] == "Y0"
        assert finding["constant_state"] == "ALWAYS_OFF"
        assert "SM401" in finding["chain"]
        assert "M100" in finding["chain"]
        assert "Y0" in finding["chain"]

        report = build_health_report(work, {"constant-chain": findings}, {}, top=10)
        assert report["top_risks"][0]["device"] == "Y0", report
        assert report["top_risks"][0]["severity"] == "high"
        assert report["scores"]["Change safety"] < 100
        assert report["scores"]["Troubleshootability"] < 100

        xref.close()
        lite.close()


def test_missing_index_lite_makes_constant_chain_inconclusive() -> None:
    xref = sqlite3.connect(":memory:")
    xref.executescript(XREF_SCHEMA)
    xref.row_factory = sqlite3.Row
    ctx = LintContext(root=Path("fixture"), rows=[], comments={}, xref=xref, lite=None)
    findings = collect_constant_chains(ctx, index_db=Path("missing.sqlite"))
    assert findings == []
    assert "constant-chain" in ctx.states
    assert not ctx.states["constant-chain"].conclusive
    xref.close()


def main() -> int:
    test_physical_y_constant_chain_is_promoted_to_doctor_top_risk()
    test_missing_index_lite_makes_constant_chain_inconclusive()
    print("2 Doctor constant-chain checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
