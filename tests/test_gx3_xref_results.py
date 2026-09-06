from __future__ import annotations

import io
import json
import sqlite3
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path

from gx3cli.gx3_format import enumerate_inline_st_sources, parse_st_text
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_xref import main as xref_main


def invoke(root: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = xref_main(["--root", str(root), "--db", str(root / "xref.sqlite"), *args])
    return code, out.getvalue()


def build_fixture(root: Path) -> None:
    with closing(sqlite3.connect(root / "001_LDDB.db")) as con:
        con.execute("create table LadderBlocks(id text, pos real, blocktype integer, data text, rowsize integer, translated integer, ConvTarget integer)")
        for pos in range(202):
            logic = {"device": "M100"} if pos < 201 else {"device": "X0"}
            output = {"type": "coil", "device": f"M{1000 + pos}" if pos < 201 else "M100"}
            data, _, _ = generate_rung(logic, output)
            con.execute("insert into LadderBlocks values (?, ?, 0, ?, 1, 0, 0)", (f"_guid/synthetic-{pos}", pos, data))
        con.commit()
    assert invoke(root, "build")[0] == 0
    with closing(sqlite3.connect(root / "xref.sqlite")) as con:
        # One unresolved index-modified access can reach an otherwise unnamed address.
        con.execute("update xref set detail='indexed by Z0' where device='M1000'")
        con.commit()


def result(root: Path, device: str, *args: str) -> tuple[int, dict]:
    code, text = invoke(root, "where-used", device, "--json", *args)
    return code, json.loads(text)["results"][0]


def test_default_limit_discloses_hidden_writer(root: Path) -> None:
    code, data = result(root, "M100")
    assert code == 0
    assert len(data["writers"]) == 0
    assert data["total_counts"] == {"writers": 1, "readers": 201, "refs": 0}, data
    assert data["total_count"] == 202 and data["returned_count"] == 200
    assert data["truncated"] is True and data["limit"] == 200
    assert any("limit" in warning for warning in data["warnings"])
    code, text = invoke(root, "where-used", "M100")
    assert "Writers (0 shown / 1 total)" in text, text
    assert "202" in text and "limit" in text, text


def test_unlimited_query_and_zero_limit(root: Path) -> None:
    code, data = result(root, "M100", "--limit", "-1")
    assert code == 0 and len(data["writers"]) == 1
    assert data["returned_count"] == 202 and data["truncated"] is False
    code, data = result(root, "M100", "--limit", "0")
    assert code == 0 and data["total_count"] == 202
    assert data["returned_count"] == 0 and data["truncated"] is True


def test_index_warning_survives_json_and_no_matches(root: Path) -> None:
    code, data = result(root, "M1000")
    assert code == 0 and any("index-modified" in w for w in data["warnings"])
    code, data = result(root, "M9999")
    assert code == 1 and data["total_count"] == 0 and not data["truncated"]
    assert data["writers"] == [] and data["readers"] == []
    assert any("index-modified" in w for w in data["warnings"])
    code, text = invoke(root, "where-used", "M9999")
    assert code == 1 and "no occurrences" in text and "index-modified" in text, text


def test_empty_json_without_indexed_access(root: Path) -> None:
    code, data = result(root, "D9999")
    assert code == 1 and data["warnings"] == []
    assert data["total_counts"] == {"writers": 0, "readers": 0, "refs": 0}


def test_range_and_read_modify_write_counts(root: Path) -> None:
    with closing(sqlite3.connect(root / "xref.sqlite")) as con:
        con.execute("update xref set access='both', range_len=3 where device='M1200'")
        con.commit()
    for device in ("M1200", "M1201", "M1202"):
        code, data = result(root, device)
        assert code == 0 and data["total_count"] == 1, data
        assert data["total_counts"]["writers"] == 1, data
        assert data["total_counts"]["readers"] == 1, data
        assert len(data["readers"]) == 1, data
        assert len(data["writers"]) == 1 and not data["truncated"], data


def test_real_read_modify_write_is_one_occurrence() -> None:
    from gx3cli.gx3_xref_read import counts_for

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Two-operand addition reads and writes its destination D600.
        data = (
            "V1:9:1:1:1:1:1:1:a:M:+:D:D:cb{fg=fg{dim=4x1:es=["
            "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=1:vt=nn}]}:pos=0,0}:"
            "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}]}:args=["
            "d{s=#:a=500:vt=nn}:d{s=#:a=600:vt=nn}]}:pos=1,0}]}}"
        )
        with closing(sqlite3.connect(root / "001_LDDB.db")) as con:
            con.execute("create table LadderBlocks(id text, pos real, blocktype integer, data text, rowsize integer, translated integer, ConvTarget integer)")
            con.execute("insert into LadderBlocks values ('synthetic-plus', 0, 0, ?, 1, 0, 0)", (data,))
            con.commit()
        assert invoke(root, "build")[0] == 0
        for limit in (0, 1, 2, -1):
            code, report = result(root, "D600", "--limit", str(limit))
            assert code == 0 and report["total_count"] == 1, report
            assert report["total_counts"] == {"writers": 1, "readers": 1, "refs": 0}, report
            assert report["truncated"] is (limit == 0), report
            if limit != 0:
                assert report["writers"][0]["id"] == report["readers"][0]["id"], report
        with closing(sqlite3.connect(root / "xref.sqlite")) as con:
            con.row_factory = sqlite3.Row
            assert counts_for(con, ["D600"])["D600"] == {"read": 1, "write": 1}
        code, text = invoke(root, "where-used", "D600")
        assert code == 0 and "Writers (1)" in text and "Readers (1)" in text, text


def test_partial_st_parser_contract() -> None:
    supported = parse_st_text(
        "D200 := D100; AlarmLatched := X10 AND InterlockOK;",
        source_kind="st",
        source_file="001_STDB.db",
        source_location="Source:rowid=1:Code",
        pou="Main",
    )
    refs = [(r.symbol, r.access) for r in supported.references]
    assert supported.coverage == "supported"
    assert refs == [
        ("D200", "write"),
        ("D100", "read"),
        ("AlarmLatched", "write"),
        ("X10", "read"),
        ("InterlockOK", "read"),
    ], refs

    unsupported = parse_st_text(
        "IF X0 THEN D0 := D1; END_IF;",
        source_kind="st",
        source_file="001_STDB.db",
        source_location="Source:rowid=2:Code",
        pou="Main",
    )
    assert unsupported.coverage == "partial"
    assert unsupported.references == [], unsupported.references
    assert any("control-flow" in reason for reason in unsupported.reasons)

    inline = enumerate_inline_st_sources(
        {"001_LDDB.db": [{"data": "D10 := M1;", "pos": 42}]},
        {"001_LDDB.db": "Main"},
    )
    assert len(inline) == 1
    assert inline[0].source_kind == "inline-st"
    assert inline[0].pou == "Main"
    assert inline[0].source_location == "pos=42:fragment=1"


def build_st_fixture(root: Path) -> None:
    with closing(sqlite3.connect(root / "001_LDDB.db")) as con:
        con.execute("create table LadderBlocks(id text, pos real, blocktype integer, data text, rowsize integer, translated integer, ConvTarget integer)")
        con.commit()
    with closing(sqlite3.connect(root / "001_STDB.db")) as con:
        con.execute("create table Source(Pou text, Code text)")
        con.execute(
            "insert into Source values (?, ?)",
            (
                "MainST",
                "D200 := D100; AlarmLatched := X10 AND InterlockOK; "
                "IF X0 THEN D0 := D1; END_IF;",
            ),
        )
        con.commit()
    code, text = invoke(root, "build")
    assert code == 0, text
    assert "ST evidence:" in text and "partial_sources=1" in text, text


def test_st_xref_bridge(root: Path) -> None:
    with closing(sqlite3.connect(root / "xref.sqlite")) as con:
        con.row_factory = sqlite3.Row
        d100 = con.execute(
            "select * from xref where device='D100' and role='ST'"
        ).fetchone()
        d200 = con.execute(
            "select * from xref where device='D200' and role='ST'"
        ).fetchone()
        assert d100 is not None and d100["access"] == "read", d100
        assert d200 is not None and d200["access"] == "write", d200
        assert d100["access_basis"] == "structured-text-partial"
        assert d100["parse_status"] == "st-partial"
        assert d100["pou"] == "MainST", d100

        source = con.execute(
            "select pou, source_location, coverage from st_sources where source_file='001_STDB.db'"
        ).fetchone()
        assert source is not None and source["pou"] == "MainST", source
        assert source["source_location"] == "Source:rowid=1:Code", source
        assert source["coverage"] == "partial", source

        refs = {
            (row["symbol"], row["access"])
            for row in con.execute("select symbol, access from st_refs")
        }
        assert ("AlarmLatched", "write") in refs
        assert ("InterlockOK", "read") in refs
        assert ("X10", "read") in refs
        # Unsupported IF/END_IF is surfaced as partial but contributes no guessed refs.
        assert ("X0", "read") not in refs
        assert all(symbol not in {"D0", "D1"} for symbol, _access in refs)

    code, data = result(root, "AlarmLatched")
    assert code == 0, data
    assert data["total_count"] == 0
    assert data["st_symbol_counts"]["writers"] == 1
    assert len(data["st_symbol_refs"]) == 1
    assert data["st_symbol_refs"][0]["pou"] == "MainST"
    assert data["coverage"]["st"]["state"] == "partial"
    assert any("ST/inline-ST coverage is partial" in warning for warning in data["warnings"])

    code, missing = result(root, "D9999")
    assert code == 1
    assert any("ST/inline-ST coverage is partial" in warning for warning in missing["warnings"])

    code, text = invoke(root, "downstream", "D100")
    assert code == 0
    assert "Downstream traversal does not infer value-flow through ST" in text, text


def main() -> int:
    test_real_read_modify_write_is_one_occurrence()
    with tempfile.TemporaryDirectory(prefix="gx3_xref_results_") as tmp:
        root = Path(tmp)
        build_fixture(root)
        test_default_limit_discloses_hidden_writer(root)
        test_unlimited_query_and_zero_limit(root)
        test_index_warning_survives_json_and_no_matches(root)
        test_empty_json_without_indexed_access(root)
        test_range_and_read_modify_write_counts(root)

    test_partial_st_parser_contract()
    with tempfile.TemporaryDirectory(prefix="gx3_xref_st_") as tmp:
        root = Path(tmp)
        build_st_fixture(root)
        test_st_xref_bridge(root)

    print("7 xref/ST completeness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
