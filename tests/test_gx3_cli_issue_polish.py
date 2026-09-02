from __future__ import annotations

import contextlib
import io
import json
import tempfile
from pathlib import Path

from gx3cli.gx3_cli import main as cli_main
from gx3cli.gx3_index_lite import main as index_main
from gx3cli.gx3_synthetic_project import create_synthetic_project
from gx3cli.gx3_xref import main as xref_main


def capture(func, args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(args)
    return int(code or 0), buf.getvalue()


def test_top_level_list_is_grouped_and_no_color_is_accepted() -> None:
    code, out = capture(cli_main, ["--no-color", "list"])
    assert code == 0
    assert "Getting Started:" in out
    assert "Search:" in out
    assert "Analysis:" in out
    assert "Reports:" in out
    assert "Diagnostics:" in out


def test_index_queries_emit_json_and_expand_synonyms() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "fixture")
        index_db = work / "fixture.sqlite"

        assert capture(index_main, ["build", "--root", str(root), "--out", str(index_db)])[0] == 0

        code, out = capture(index_main, ["device", "M100", "--db", str(index_db), "--root", str(root), "--json"])
        assert code == 0
        payload = json.loads(out)
        assert payload["command"] == "query-device"
        assert payload["root"] == str(root)
        assert payload["results"][0]["device"] == "M100"

        code, out = capture(index_main, ["comment", "start", "--db", str(index_db), "--root", str(root), "--json", "--expand-synonyms"])
        assert code == 0
        payload = json.loads(out)
        assert payload["command"] == "query-comment"
        assert isinstance(payload["results"], list)


def test_xref_where_used_emits_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "fixture")
        xref_db = work / "fixture_xref.sqlite"

        assert capture(xref_main, ["--root", str(root), "--db", str(xref_db), "build"])[0] == 0
        code, out = capture(xref_main, ["--root", str(root), "--db", str(xref_db), "where-used", "M100", "--json"])
        assert code == 0
        payload = json.loads(out)
        assert payload["command"] == "xref where-used"
        assert payload["root"] == str(root)
        assert payload["results"][0]["device"] == "M100"
        assert "writers" in payload["results"][0]
        assert any(row["access_basis"] == "ladder contact/coil" for row in payload["results"][0]["readers"])


def main() -> None:
    test_top_level_list_is_grouped_and_no_color_is_accepted()
    test_index_queries_emit_json_and_expand_synonyms()
    test_xref_where_used_emits_json()
    print("CLI polish checks passed")


if __name__ == "__main__":
    main()
