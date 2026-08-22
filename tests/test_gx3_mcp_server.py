from __future__ import annotations

from gx3cli.gx3_mcp_server import handle


def test_mcp_initialize_and_tool_list() -> None:
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "gx3-cli-mcp"

    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert "gx3_run_command" in names
    assert "gx3_trace_device" in names


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


def test_mcp_rejects_external_report_command() -> None:
    response = handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "gx3_run_command", "arguments": {"command": "report-issue", "args": ["--dry-run"]}},
        }
    )
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert "sends support reports outside the local machine" in result["content"][0]["text"]


def main() -> int:
    test_mcp_initialize_and_tool_list()
    test_mcp_rejects_mutating_command()
    test_mcp_rejects_local_state_command()
    test_mcp_rejects_external_report_command()
    print("MCP server checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
