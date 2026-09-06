from __future__ import annotations

"""#94: an MCP analysis command may not become an arbitrary local file writer.

The important boundary is tested through ``handle()`` rather than by calling
one output helper directly.  The generic runner has many commands/subcommands
and output spellings, so the assertions below pin the common execution path.
"""

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_mcp_server import handle
from test_gx3_shared_reach import coil, write_program


@contextmanager
def mcp_output(path: Path):
    old = os.environ.get("GX3_MCP_OUTPUT_DIR")
    os.environ["GX3_MCP_OUTPUT_DIR"] = str(path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("GX3_MCP_OUTPUT_DIR", None)
        else:
            os.environ["GX3_MCP_OUTPUT_DIR"] = old


def project(work: Path) -> Path:
    root = work / "project"
    write_program(root, [("_guid/a", coil("a", 1, 100))])
    (root / "UnitConfig.dat").write_bytes(b"fixture")
    return root


def generic(command: str, args: list[str], root: Path) -> dict[str, object]:
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 94,
            "method": "tools/call",
            "params": {
                "name": "gx3_run_command",
                "arguments": {
                    "command": command,
                    "args": args,
                    "root": str(root),
                },
            },
        }
    )
    assert response is not None
    return response["result"]


def text(result: dict[str, object]) -> str:
    content = result["content"]
    assert isinstance(content, list) and content
    return str(content[0]["text"])


def test_support_bundle_cannot_overwrite_an_outside_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        protected = work / "do-not-touch.txt"
        protected.write_text("keep me", encoding="utf-8")
        root = project(work)

        with mcp_output(sandbox):
            result = generic("support-bundle", ["-o", str(protected)], root)

        assert result["isError"] is True, result
        assert "sandbox blocked" in text(result), text(result)
        assert protected.read_text(encoding="utf-8") == "keep me"


def test_relative_traversal_cannot_escape_the_output_directory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        escaped = work / "escaped.zip"
        root = project(work)

        with mcp_output(sandbox):
            result = generic("support-bundle", ["-o", "../escaped.zip"], root)

        assert result["isError"] is True, result
        assert not escaped.exists()


def test_symlink_escape_is_rejected_after_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        outside = work / "outside"
        sandbox.mkdir()
        outside.mkdir()
        link = sandbox / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Windows may require Developer Mode/admin for Python symlink
            # creation. The platform limitation is documented separately.
            return
        root = project(work)

        with mcp_output(sandbox):
            result = generic("support-bundle", ["-o", "escape/bundle.zip"], root)

        assert result["isError"] is True, result
        assert not (outside / "bundle.zip").exists()


def test_normal_relative_output_is_created_inside_the_sandbox() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        root = project(work)

        with mcp_output(sandbox):
            result = generic("support-bundle", ["-o", "support.zip"], root)

        assert result["isError"] is False, text(result)
        assert (sandbox / "support.zip").is_file()
        assert not (work / "support.zip").exists()


def test_xref_build_cannot_create_an_outside_database() -> None:
    # This is the subcommand case from #94. ``xref`` is allowed, and --db is
    # an input for some verbs and an output for ``build``. Flag-name filtering
    # cannot represent that distinction; the filesystem boundary can.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        outside_db = work / "xref.sqlite"
        root = project(work)

        with mcp_output(sandbox):
            result = generic("xref", ["build", "--db", str(outside_db)], root)

        assert result["isError"] is True, result
        assert not outside_db.exists()


def test_index_lite_out_alias_cannot_escape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        outside_db = work / "index.sqlite"
        root = project(work)

        with mcp_output(sandbox):
            result = generic("index-lite", ["build", "--out", str(outside_db)], root)

        assert result["isError"] is True, result
        assert not outside_db.exists()


def test_typed_tool_uses_the_same_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        protected = work / "ladder.txt"
        protected.write_text("keep me", encoding="utf-8")
        root = project(work)

        with mcp_output(sandbox):
            response = handle(
                {
                    "jsonrpc": "2.0",
                    "id": 95,
                    "method": "tools/call",
                    "params": {
                        "name": "gx3_ladder_print",
                        "arguments": {
                            "program": "001_LDDB.db",
                            "root": str(root),
                            "output": str(protected),
                        },
                    },
                }
            )

        assert response is not None
        result = response["result"]
        assert result["isError"] is True, result
        assert protected.read_text(encoding="utf-8") == "keep me"


def main() -> int:
    test_support_bundle_cannot_overwrite_an_outside_file()
    test_relative_traversal_cannot_escape_the_output_directory()
    test_symlink_escape_is_rejected_after_resolution()
    test_normal_relative_output_is_created_inside_the_sandbox()
    test_xref_build_cannot_create_an_outside_database()
    test_index_lite_out_alias_cannot_escape()
    test_typed_tool_uses_the_same_boundary()
    print("MCP filesystem sandbox checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
