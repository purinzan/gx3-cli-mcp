from __future__ import annotations

import io
import json
import sqlite3
import tempfile
from contextlib import closing, redirect_stdout
from pathlib import Path

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
        assert len(data["writers"]) == 1 and not data["truncated"], data


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gx3_xref_results_") as tmp:
        root = Path(tmp)
        build_fixture(root)
        test_default_limit_discloses_hidden_writer(root)
        test_unlimited_query_and_zero_limit(root)
        test_index_warning_survives_json_and_no_matches(root)
        test_empty_json_without_indexed_access(root)
        test_range_and_read_modify_write_counts(root)
    print("5 xref result completeness checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
