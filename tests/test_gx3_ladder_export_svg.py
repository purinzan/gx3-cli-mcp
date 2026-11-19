from __future__ import annotations

"""SVG discovery and compatibility through the real CLI and MCP subprocesses.

Fixtures are synthetic LDDB rows or the public demo generator. No renderer or
MCP filesystem guard is replaced: byte equality and rejected writes exercise
the same paths a CLI user or MCP client takes.
"""

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_mcp_server import handle
from gx3cli.gx3_synthetic_project import create_synthetic_gx3_archive
from test_gx3_cli_root_passthrough import run_cli
from test_gx3_mcp_filesystem import mcp_output, text
from test_gx3_shared_reach import coil, write_program


PROGRAM = "001_LDDB.db"


def project(work: Path) -> Path:
    root = work / "synthetic project"
    write_program(root, [
        ("_guid/one", coil("a", 1, 100)),
        ("_guid/two", coil("b", 2, 101)),
        ("_guid/three", coil("a", 3, 102)),
    ])
    (root / "UnitConfig.dat").write_text("synthetic fixture\n", encoding="utf-8")
    return root


def cli(work: Path, command: str, root: Path, *args: str) -> str:
    result = run_cli(work, [command, PROGRAM, "--root", str(root), *args])
    assert result.returncode == 0, result.stdout
    return result.stdout


def assert_svg(value: str, *devices: str) -> None:
    svg = ET.fromstring(value)
    assert svg.tag == "{http://www.w3.org/2000/svg}svg", svg.tag
    labels = set(svg.itertext())
    for device in devices:
        assert device in labels, (device, labels)


def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
    response = handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert response is not None
    return response["result"]


def export_call(route: str, root: Path, **options: str) -> dict[str, object]:
    if route == "typed":
        return call("gx3_ladder_export_svg", {"root": str(root), "program": PROGRAM, **options})
    args = [PROGRAM]
    if route == "ladder-layout":
        args += ["--format", "svg"]
    for key, value in options.items():
        args += [{"output": "-o", "pos_range": "--pos-range", "device": "--device"}[key], value]
    return call("gx3_run_command", {"command": route, "root": str(root), "args": args})


def test_cli_help_names_svg_export_and_retains_coordinate_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for args in (["--help"], ["list"]):
            result = run_cli(work, args)
            assert result.returncode == 0, result.stdout
            assert "ladder-export-svg" in result.stdout, result.stdout
            assert "ladder-layout" in result.stdout, result.stdout
            assert "SVG" in result.stdout and "JSON" in result.stdout, result.stdout
        for command in ("ladder-export-svg", "ladder-layout"):
            for args in ([command, "--help"], ["help", command]):
                result = run_cli(work, args)
                assert result.returncode == 0, result.stdout
                assert f"gx3-cli {command}" in result.stdout, result.stdout
                assert "ladder-layout" in result.stdout and "ladder-export-svg" in result.stdout
                assert "--device" in result.stdout and "--pos-range" in result.stdout


def test_cli_svg_is_identical_and_old_json_default_is_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        old_svg = cli(work, "ladder-layout", root, "--format", "svg")
        new_svg = cli(work, "ladder-export-svg", root)
        assert_svg(new_svg, "M100", "M101", "M102")
        assert new_svg == old_svg
        assert cli(work, "ladder-export-svg", root, "--format", "svg") == old_svg
        default_json = cli(work, "ladder-layout", root)
        assert default_json == cli(work, "ladder-layout", root, "--format", "json")
        payload = json.loads(default_json)
        assert payload["program"] == PROGRAM and len(payload["rungs"]) == 3, payload


def test_cli_filters_and_empty_selection_share_the_existing_exporter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        for flags, count in (
            (("--device", "M101"), 1),
            (("--pos-range", "16-16"), 1),
            (("--device", "M101", "--pos-range", "0-16"), 1),
            (("--device", "M999"), 0),
        ):
            old_svg = cli(work, "ladder-layout", root, "--format", "svg", *flags)
            new_svg = cli(work, "ladder-export-svg", root, *flags)
            assert new_svg == old_svg
            assert_svg(new_svg, *("M101",) if count else ())
            payload = json.loads(cli(work, "ladder-layout", root, *flags))
            assert len(payload["rungs"]) == count, payload
            assert "M100" not in new_svg and "M102" not in new_svg


def test_cli_output_files_match_stdout_with_both_output_spellings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        expected = cli(work, "ladder-export-svg", root)
        for command in ("ladder-export-svg", "ladder-layout"):
            for flag in ("-o", "--output"):
                output = work / command / flag.lstrip("-") / "ラダー image.svg"
                extra = ("--format", "svg") if command == "ladder-layout" else ()
                result = cli(work, command, root, *extra, flag, str(output))
                assert "written:" in result and "3 rungs" in result, result
                assert output.read_bytes() == expected.encode("utf-8")


def test_readme_demo_archive_works_with_the_svg_entry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        archive = work / "demo.gx3"
        generated = run_cli(work, ["synthetic-project", str(archive), "--profile", "demo-line"])
        assert generated.returncode == 0, generated.stdout
        output = work / "ladder.svg"
        cli(work, "ladder-export-svg", archive, "-o", str(output))
        expected = cli(work, "ladder-layout", archive, "--format", "svg")
        assert output.read_bytes() == expected.encode("utf-8")
        assert_svg(expected)
        assert "<text" in expected


def test_svg_entry_rejects_missing_program_bad_filters_and_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        for args in (
            ["--root", str(root)],
            [PROGRAM, "--root", str(root), "--format", "json"],
            [PROGRAM, "--root", str(root), "--pos-range", "bad"],
            [PROGRAM, "--root", str(root), "--unknown"],
        ):
            result = run_cli(work, ["ladder-export-svg", *args])
            assert result.returncode != 0, result.stdout
            assert "<svg" not in result.stdout, result.stdout
        output = work / "no-json.svg"
        result = run_cli(work, ["ladder-export-svg", PROGRAM, "--root", str(root),
                                "--format", "json", "-o", str(output)])
        assert result.returncode != 0 and not output.exists(), result.stdout


def test_mcp_discovers_dedicated_svg_tool_and_keeps_the_generic_routes() -> None:
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert response is not None
    tools = {tool["name"]: tool for tool in response["result"]["tools"]}
    svg = tools["gx3_ladder_export_svg"]
    assert "SVG" in svg["description"] and "gx3-cli ladder-export-svg" in svg["description"]
    assert svg["inputSchema"]["required"] == ["program", "root"]
    assert "GX3_MCP_OUTPUT_DIR" in svg["inputSchema"]["properties"]["output"]["description"]
    assert "gx3_ladder_export_svg" in tools["gx3_ladder_print"]["description"]
    assert "ladder-layout" in tools["gx3_run_command"]["description"]
    commands = text(call("gx3_list_commands", {}))
    assert "ladder-export-svg" in commands and "ladder-layout" in commands, commands
    init = handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    assert init is not None
    instructions = init["result"]["instructions"]
    assert "gx3_ladder_export_svg" in instructions and "gx3_ladder_print" in instructions
    assert "NEVER name a device without its device comment" in instructions
    assert "never with JSON" in instructions


def test_mcp_stdout_and_filtering_match_cli_for_all_three_routes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        sandbox = work / "mcp-output"
        for options in ({}, {"device": "M101"}, {"pos_range": "16-16"}):
            flags = [part for key, value in options.items()
                     for part in ({"device": "--device", "pos_range": "--pos-range"}[key], value)]
            expected = cli(work, "ladder-export-svg", root, *flags).strip()
            with mcp_output(sandbox):
                for route in ("typed", "ladder-export-svg", "ladder-layout"):
                    result = export_call(route, root, **options)
                    assert result["isError"] is False, text(result)
                    assert text(result).removeprefix("exit_code=0\n").strip() == expected, text(result)
        expected_json = json.loads(cli(work, "ladder-layout", root))
        with mcp_output(sandbox):
            result = call("gx3_run_command", {"command": "ladder-layout", "root": str(root), "args": [PROGRAM]})
        assert result["isError"] is False, text(result)
        # MCP canonicalizes its root before launching the CLI. On macOS the
        # same temporary folder has /var and /private/var spellings.
        assert json.loads(text(result).removeprefix("exit_code=0\n")) == {
            **expected_json, "root": str(root.resolve()),
        }


def test_mcp_archives_keep_the_existing_extraction_cache_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        sandbox = work / "mcp-output"
        sandbox.mkdir()
        inside = create_synthetic_gx3_archive(sandbox / "inside.gx3")
        outside = create_synthetic_gx3_archive(work / "outside.gx3")
        with mcp_output(sandbox):
            for route in ("typed", "ladder-export-svg", "ladder-layout"):
                result = export_call(route, inside, output=f"{route}.svg")
                assert result["isError"] is False, text(result)
                assert_svg((sandbox / f"{route}.svg").read_text(encoding="utf-8"))
                blocked = export_call(route, outside, output="blocked.svg")
                assert blocked["isError"] is True, text(blocked)
        expected = cli(work, "ladder-layout", inside, "--format", "svg").encode("utf-8")
        for route in ("typed", "ladder-export-svg", "ladder-layout"):
            assert (sandbox / f"{route}.svg").read_bytes() == expected
        assert not (sandbox / "blocked.svg").exists()
        assert not (work / ".gx3_cache").exists()


def test_mcp_saves_inside_output_dir_and_rejects_absolute_and_relative_escapes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        sandbox = work / "mcp-output"
        protected = work / "keep.svg"
        protected.write_text("keep me", encoding="utf-8")
        expected = cli(work, "ladder-export-svg", root).encode("utf-8")
        with mcp_output(sandbox):
            for route in ("typed", "ladder-export-svg", "ladder-layout"):
                for target in (f"{route}/ラダー image.svg", str(sandbox / f"{route}.svg")):
                    result = export_call(route, root, output=target)
                    assert result["isError"] is False, text(result)
                    assert (sandbox / target).read_bytes() == expected
                for target in (str(protected), "../keep.svg"):
                    result = export_call(route, root, output=target)
                    assert result["isError"] is True, text(result)
                    assert "sandbox blocked" in text(result), text(result)
                    assert protected.read_text(encoding="utf-8") == "keep me"


def test_mcp_symlink_output_escape_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = project(work)
        sandbox = work / "mcp-output"
        outside = work / "outside"
        sandbox.mkdir()
        outside.mkdir()
        link = sandbox / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            print("SKIP SVG symlink test: platform does not permit creating symlinks")
            return
        with mcp_output(sandbox):
            for route in ("typed", "ladder-export-svg", "ladder-layout"):
                result = export_call(route, root, output="escape/ladder.svg")
                assert result["isError"] is True, text(result)
                assert "sandbox blocked" in text(result), text(result)
        assert not (outside / "ladder.svg").exists()


def main() -> int:
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    for _name, test in tests:
        test()
    print(f"{len(tests)} ladder SVG export checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
