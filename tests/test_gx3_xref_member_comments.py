from __future__ import annotations

"""Real synthetic LDDB/comment DB -> xref builder -> member-aware readers."""

import csv
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from gx3cli.gx3_alarm_map import device_comment_from
from gx3cli.gx3_scan_order import load_all_device_rows, load_rows_for_device, device_comment
from gx3cli.gx3_synthetic_project import create_synthetic_project
from gx3cli.gx3_timing_chart import first_comment
from gx3cli.gx3_xref import rows_for_device
from gx3cli.gx3_xref_read import occurrences_of
from gx3cli.review_gx3_project import DEVICE_CODE_BY_TYPE

REPO = Path(__file__).resolve().parents[1]
BASE_COMMENT = "Synthetic range base"
MEMBER_COMMENT = "Synthetic member 194"
END_COMMENT = "Synthetic last member"


def create_fixture(root: Path) -> Path:
    create_synthetic_project(root)
    # D= K0 K8L177: A32 types preserve the displayed comparison width;
    # K8 selects 32 physical bits. This is generated data, not a customer row.
    tokens = ["=", "K_1", "L", "Ks", "c", "Y"]
    header = "V1:" + str(len(tokens)) + ":" + ":".join(str(len(t)) for t in tokens) + ":" + ":".join(tokens)
    data = header + (
        ":cb{fg=fg{dim=3x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=A32s}:as{vt=A32s}]}:args=["
        "c{s=#:v=0}:M{b=d{s=#:a=177:vt=nn}:m=c{s=#:v=8}}]}:pos=0,0}:"
        "e{s=wire:pos=1,0}:"
        "e{s=ce{op=cl{op=#:ct=c:as=[as{vt=Abl}]}:args=[d{s=#:a=0:vt=nn}]}:pos=2,0}]}}"
    )
    with closing(sqlite3.connect(root / "001_LDDB.db")) as con, con:
        con.execute("DELETE FROM LadderBlocks")
        con.execute("INSERT INTO LadderBlocks VALUES (?,1,0,?,1,0,0)",
                    ("_guid/00000000-0000-0000-0000-000000000001", data))
    with closing(sqlite3.connect(root / "001_DC.db")) as con, con:
        con.execute("DELETE FROM DEVICE_DATA")
        con.execute("DELETE FROM COMMENT_DATA")
        for seq, number, text in ((1, 177, BASE_COMMENT), (2, 194, MEMBER_COMMENT), (3, 208, END_COMMENT)):
            con.execute("INSERT INTO DEVICE_DATA VALUES (?,?,0,0,?,0)", (seq, DEVICE_CODE_BY_TYPE["L"], number))
            con.execute("INSERT INTO COMMENT_DATA VALUES (?,5,?,0)", (seq, text))
    return root


def cli(root: Path, db: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-B", "-m", "gx3cli.gx3_cli", "xref", "--root", str(root), "--db", str(db), *args],
        cwd=root.parent, env=dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8"),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert not result.stderr or result.returncode != 0, result.stderr
    return result.returncode, result.stdout


def test_member_comments_are_stored_and_queried_through_real_builder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_fixture(work / "project")
        db = work / "xref.sqlite"
        code, text = cli(root, db, "build")
        assert code == 0, text
        for device, expected in (("L177", BASE_COMMENT), ("L194", MEMBER_COMMENT),
                                 ("L195", ""), ("L208", END_COMMENT)):
            code, text = cli(root, db, "where-used", device, "--json")
            assert code == 0, text
            result = json.loads(text)["results"][0]
            assert result["comment"] == expected, (device, result)
            assert result["readers"] and all(r["comment"] == expected for r in result["readers"])
            assert result["readers"][0]["device"] == "L177"  # original operand stays intact
            code, plain = cli(root, db, "where-used", device)
            assert code == 0 and plain.splitlines()[0] == f"{device} {expected}".rstrip(), plain
            with closing(sqlite3.connect(db)) as con:
                con.row_factory = sqlite3.Row
                assert all(r["comment"] == expected for r in occurrences_of(con, device))
                assert all(r["comment"] == expected for r in rows_for_device(con, device, 10))
                assert device_comment_from(con, device) == first_comment(con, device) == expected
                assert device_comment(load_rows_for_device(con, device)) == expected
                assert device_comment(load_all_device_rows(con)[device]) == expected
                members = con.execute("SELECT comment FROM xref_members WHERE member_device=?", (device,)).fetchall()
                assert members and all(r["comment"] == expected for r in members)
        for device in ("L176", "L209"):
            code, text = cli(root, db, "where-used", device, "--json")
            assert code == 1 and json.loads(text)["results"][0]["total_count"] == 0
        # The raw stored occurrence still describes the named base, not L194.
        export = work / "raw.csv"
        code, text = cli(root, db, "export", "--device", "L177", "--output", str(export))
        assert code == 0, text
        with export.open(encoding="utf-8-sig", newline="") as f:
            assert all(r["comment"] == BASE_COMMENT for r in csv.DictReader(f))


def test_legacy_member_or_interval_index_does_not_borrow_base_comment() -> None:
    """Low-level compatibility contract, not a substitute for builder tests."""
    for members in (False, True):
        with closing(sqlite3.connect(":memory:")) as con:
            con.row_factory = sqlite3.Row
            con.execute("CREATE TABLE xref(id INT, device TEXT, device_type TEXT, number INT, range_len INT, access TEXT, pou TEXT, pos INT, comment TEXT)")
            con.execute("INSERT INTO xref VALUES (1,'L177','L',177,32,'read','p',1,?)", (BASE_COMMENT,))
            if members:
                con.execute("CREATE TABLE xref_members(src_id INT, member_device TEXT, device_type TEXT, number INT, run_offset INT)")
                con.executemany("INSERT INTO xref_members VALUES (1,?,'L',?,?)",
                                [(f"L{n}", n, n - 177) for n in range(177, 209)])
                assert occurrences_of(con, "L194")[0]["comment"] == ""
                assert occurrences_of(con, "L177")[0]["comment"] == BASE_COMMENT
            assert rows_for_device(con, "L194", 10)[0]["comment"] == ""
            assert rows_for_device(con, "L177", 10)[0]["comment"] == BASE_COMMENT
            assert device_comment_from(con, "L194") == first_comment(con, "L194") == ""


if __name__ == "__main__":
    test_member_comments_are_stored_and_queried_through_real_builder()
    test_legacy_member_or_interval_index_does_not_borrow_base_comment()
    print("xref member comment regression checks passed")
