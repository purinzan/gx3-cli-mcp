from __future__ import annotations

"""The offline live-read modes reach MCP; the network mode does not.

`live-read` was excluded from MCP as a whole because it opens a socket. Two of
its three modes never do -- they read files that were captured earlier -- so the
exclusion was costing two working offline analyses for no safety gained.

The gate moves from the command name to the mode word. That is the part worth
holding still: an agent must not be able to reach the network reader by asking
for the command it now sees in the allow-list. These check both directions.

What is deliberately not done: putting `live-read` into READ_ONLY_COMMANDS. The
name would then be allowed and every argument with it, which is precisely the
thing the mode gate exists to prevent.
"""

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_mcp_server import (
    EXTERNAL_IO_COMMANDS,
    OFFLINE_EXTERNAL_IO_MODES,
    READ_ONLY_COMMANDS,
    TYPED_BY_NAME,
    command_summary,
    handle,
    offline_mode_allowed,
)


def call(name: str, arguments: dict) -> dict:
    response = handle(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
         "params": {"name": name, "arguments": arguments}}
    )
    assert response is not None
    return response["result"]


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


def test_mixed_capture_is_rejected_through_both_mcp_entries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "capture.json"
        base = {"timestamp": "2026-09-06T10:00:00Z", "device": "M1", "value": False}
        path.write_text(json.dumps({"records": [
            {**base, "source": "PLC-A"}, {**base, "source": "PLC-B", "value": True},
        ]}), encoding="utf-8")
        for name, args in (
            ("gx3_replay_capture", {"verb": "changes", "input": str(path)}),
            ("gx3_run_command", {"command": "live-read", "args": ["replay", "changes", str(path)]}),
        ):
            result = call(name, args)
            assert result["isError"] is True, result
            assert "mixed capture identity" in text_of(result), result


# ----- the network mode stays out -------------------------------------------


def test_the_bare_command_is_still_refused() -> None:
    result = call("gx3_run_command", {"command": "live-read", "args": []})
    assert result["isError"] is True, result
    assert "connects to external equipment" in text_of(result), text_of(result)


def test_the_network_mode_is_refused_by_its_own_flags() -> None:
    result = call(
        "gx3_run_command",
        {"command": "live-read", "args": ["--ip", "192.0.2.10", "--device", "D100"]},
    )
    assert result["isError"] is True, result
    assert "connects to external equipment" in text_of(result), text_of(result)


def test_a_network_flag_smuggled_behind_a_mode_word_is_refused() -> None:
    # The mode parsers reject an unknown flag anyway. The boundary is read a
    # second time here, in the place the boundary is decided, so a later parser
    # change cannot quietly open it.
    for args in (
        ["explain", "M100", "--ip", "192.0.2.10"],
        ["explain", "M100", "--ip=192.0.2.10"],
        ["replay", "changes", "log.csv", "--port", "5000"],
    ):
        assert offline_mode_allowed("live-read", args) is False, args


def test_the_command_stays_out_of_the_read_only_set() -> None:
    assert "live-read" in EXTERNAL_IO_COMMANDS
    assert "live-read" not in READ_ONLY_COMMANDS


# ----- the offline modes get in ----------------------------------------------


def test_the_offline_mode_words_are_allowed() -> None:
    for mode in ("explain", "replay", "modes"):
        assert offline_mode_allowed("live-read", [mode]) is True, mode


def test_an_invented_mode_word_is_not() -> None:
    for mode in ("write", "connect", "", "EXPLAIN"):
        assert offline_mode_allowed("live-read", [mode]) is False, mode


def test_the_typed_tools_are_offered() -> None:
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "gx3_explain_snapshot" in names, names
    assert "gx3_replay_capture" in names, names


def test_the_typed_tools_can_only_build_an_offline_call() -> None:
    """Whatever an agent passes, the first argument is the mode word."""
    explain = TYPED_BY_NAME["gx3_explain_snapshot"]
    args = explain.build_args({"device": "M100", "root": "p", "snapshot": "s.json"})
    assert args[0] == "explain", args
    assert offline_mode_allowed(explain.command, args), args

    replay = TYPED_BY_NAME["gx3_replay_capture"]
    args = replay.build_args({"verb": "changes", "input": "log.csv"})
    assert args[0] == "replay", args
    assert offline_mode_allowed(replay.command, args), args

    args = replay.build_args({"verb": "snapshot", "input": "log.csv", "at": "2026-09-07T09:00:00"})
    assert args[:2] == ["replay", "snapshot"], args
    assert "--at" in args, args
    assert offline_mode_allowed(replay.command, args), args


def test_the_tools_say_they_never_connect() -> None:
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools is not None
    by_name = {tool["name"]: tool["description"] for tool in tools["result"]["tools"]}
    for name in ("gx3_explain_snapshot", "gx3_replay_capture"):
        assert "never opens a PLC connection" in by_name[name], by_name[name]


def test_the_snapshot_tool_carries_what_a_snapshot_cannot_answer() -> None:
    # An agent handed one instant will otherwise answer "why did it trip" with
    # it. Both limits belong in the description, where they are read at call
    # time rather than only in the output.
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools is not None
    by_name = {tool["name"]: tool["description"] for tool in tools["result"]["tools"]}
    explain = by_name["gx3_explain_snapshot"]
    assert "no measured value" in explain, explain
    assert "never the cause of a past" in explain, explain
    replay = by_name["gx3_replay_capture"]
    assert "not a scan-synchronized recording" in replay, replay


def test_the_command_list_names_the_modes() -> None:
    # The allow-list is the only inventory an agent can read. A command absent
    # from it is a command the agent keeps guessing at and keeps being refused.
    body = command_summary()
    for mode in sorted(OFFLINE_EXTERNAL_IO_MODES["live-read"]):
        assert f"live-read {mode}" in body, body
    assert "network mode is not available" in body, body


def main() -> int:
    test_mixed_capture_is_rejected_through_both_mcp_entries()
    test_the_bare_command_is_still_refused()
    test_the_network_mode_is_refused_by_its_own_flags()
    test_a_network_flag_smuggled_behind_a_mode_word_is_refused()
    test_the_command_stays_out_of_the_read_only_set()
    test_the_offline_mode_words_are_allowed()
    test_an_invented_mode_word_is_not()
    test_the_typed_tools_are_offered()
    test_the_typed_tools_can_only_build_an_offline_call()
    test_the_tools_say_they_never_connect()
    test_the_snapshot_tool_carries_what_a_snapshot_cannot_answer()
    test_the_command_list_names_the_modes()
    print("MCP offline mode checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
