from __future__ import annotations

import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from gx3cli.gx3_format import build_format_inventory
from gx3cli.gx3_graph import build_structure, main as graph_main
from gx3cli.gx3_lint import main as lint_main
from gx3cli.gx3_synthetic_project import create_synthetic_project


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

    print("format, graph, and lint-list checks passed")


if __name__ == "__main__":
    main()
