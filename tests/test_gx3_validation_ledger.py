from __future__ import annotations

import contextlib
import io
import json

from gx3cli.gx3_validation_ledger import REQUIRED_GROUPS, main, summarize


def capture(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(args)
    return int(code or 0), buf.getvalue()


def test_validation_ledger_reports_issue_49_closeable() -> None:
    summary = summarize()
    assert summary["issue"] == 49
    assert summary["required_groups"] == len(REQUIRED_GROUPS)
    assert summary["checked_groups"] == len(REQUIRED_GROUPS)
    assert summary["closeable"] is True
    assert summary["missing_groups"] == []
    assert all(group["evidence"] for group in summary["groups"].values())


def test_validation_ledger_cli_json_and_text() -> None:
    code, text = capture([])
    assert code == 0
    assert "Issue #49 independent validation: 8/8 groups checked" in text
    assert "Closeable: yes" in text
    assert "Missing groups:" not in text

    code, raw = capture(["--format", "json"])
    assert code == 0
    payload = json.loads(raw)
    assert payload["closeable"] is True
    assert payload["groups"]["outputs_and_multiple_writers"]["status"] == "checked"


def main_tests() -> None:
    test_validation_ledger_reports_issue_49_closeable()
    test_validation_ledger_cli_json_and_text()
    print("validation-ledger checks passed")


if __name__ == "__main__":
    main_tests()
