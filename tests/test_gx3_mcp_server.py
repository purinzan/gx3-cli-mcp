from __future__ import annotations

from gx3cli.gx3_mcp_server import handle, SERVER_INSTRUCTIONS


def test_mcp_initialize_and_tool_list() -> None:
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "gx3-cli-mcp"

    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "gx3_run_command" in names
    assert "gx3_trace_device" in names


def test_initialize_carries_the_answer_rules() -> None:
    """The two presentation rules only reach the model if initialize ships them,
    so assert on the transport, not just on the constant."""
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None
    instructions = init["result"]["instructions"]
    assert instructions == SERVER_INSTRUCTIONS
    # Rule 1: device comments are never dropped.
    assert "NEVER name a device without its device comment" in instructions
    assert "(no comment)" in instructions
    # Rule 2: ladder is shown as ladder text, not as JSON.
    assert "never with JSON" in instructions
    assert "--format text" in instructions


def test_ladder_and_device_tools_restate_the_rules() -> None:
    """A client that ignores `instructions` still sees the rules at call time."""
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools is not None
    by_name = {tool["name"]: tool["description"] for tool in tools["result"]["tools"]}
    assert "never a JSON dump" in by_name["gx3_ladder_print"]
    assert "comment" in by_name["gx3_xref_where_used"]
    assert "comment" in by_name["gx3_trace_device"]


def test_mcp_rejects_mutating_command() -> None:
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "gx3_run_command", "arguments": {"command": "intermediate", "args": []}},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert "modifies the project" in result["content"][0]["text"]


def test_mcp_rejects_local_state_command() -> None:
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "gx3_run_command", "arguments": {"command": "synthetic-project", "args": ["demo.gx3"]}},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert "creates local demo artifacts" in result["content"][0]["text"]


def main() -> int:
    test_mcp_initialize_and_tool_list()
    test_initialize_carries_the_answer_rules()
    test_ladder_and_device_tools_restate_the_rules()
    test_mcp_rejects_mutating_command()
    test_mcp_rejects_local_state_command()
    print("MCP server checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
