from __future__ import annotations

import io
import argparse
import tempfile
import json
from contextlib import redirect_stdout
from pathlib import Path

from gx3cli.gx3_format import build_format_inventory
from gx3cli.gx3_device_dictionary import collect_dictionary, main as dictionary_main
from gx3cli.gx3_graph import build_structure, main as graph_main
from gx3cli.gx3_ladder_print import main as ladder_print_main
from gx3cli.gx3_lint import main as lint_main
from gx3cli.gx3_synthetic_project import create_synthetic_project
from gx3cli.gx3_xref import build as xref_build


def capture_stdout(func, *args) -> tuple[int, str]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        code = func(*args)
    return code, stream.getvalue()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = create_synthetic_project(Path(tmp) / "fixture")

        inventory = build_format_inventory(root)
        assert inventory.has_ladder
        assert inventory.lddb_count == 1
        assert inventory.dc_count == 1
        assert not inventory.has_non_ladder_programs
        assert inventory.as_dict()["LDDB"] == 1

        structure = build_structure(root)
        assert structure["format_inventory"]["LDDB"] == 1
        assert structure["root"] == str(root)

        code, out = capture_stdout(graph_main, ["--root", str(root), "--type", "structure", "--format", "mermaid"])
        assert code == 0
        assert "flowchart TD" in out
        assert "LDDB=1" in out

        code, out = capture_stdout(
            graph_main,
            ["--root", str(root), "--type", "device-flow", "--device", "M100", "--format", "json", "--max-devices", "20"],
        )
        assert code == 0
        assert '"device": "M100"' in out

        code, out = capture_stdout(lint_main, ["--list-checks"])
        assert code == 0
        assert "GX0001" in out
        assert "duplicate-coil" in out

        xref_db = Path(tmp) / "xref.sqlite"
        code, _out = capture_stdout(xref_build, argparse.Namespace(root=str(root), db=str(xref_db)))
        assert code == 0

        dictionary = collect_dictionary(root, xref_db)
        m100 = next(row for row in dictionary if row["address"] == "M100")
        assert m100["occurrences"] >= 1
        assert m100["source"] in {"gx3-comment+xref", "xref"}

        code, out = capture_stdout(dictionary_main, ["--root", str(root), "--xref-db", str(xref_db)])
        assert code == 0
        assert '"devices"' in out
        assert '"address": "M100"' in out

        code, out = capture_stdout(
            ladder_print_main,
            ["--root", str(root), "--format", "json", "001_LDDB.db"],
        )
        assert code == 0
        assert '"live_overlay"' in out
        assert '"program": "001_LDDB.db"' in out

        live_json = Path(tmp) / "live.json"
        live_json.write_text(json.dumps({"values": {"X48": True}}, ensure_ascii=False), encoding="utf-8")
        code, out = capture_stdout(
            ladder_print_main,
            ["--root", str(root), "--format", "json", "--live-values", str(live_json), "001_LDDB.db"],
        )
        assert code == 0
        assert '"device": "X48"' in out
        assert '"condition": "pass"' in out

    print("format, graph, and lint-list checks passed")


if __name__ == "__main__":
    main()
