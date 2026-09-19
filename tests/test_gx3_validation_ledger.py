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


def test_validation_ledger_reports_issue_49_not_closeable_yet() -> None:
    summary = summarize()
    assert summary["issue"] == 49
    assert summary["required_groups"] == len(REQUIRED_GROUPS)
    assert summary["checked_groups"] == 1
    assert summary["closeable"] is False
    assert "outputs_and_multiple_writers" not in summary["missing_groups"]
    assert "indexed_and_bit_devices" in summary["missing_groups"]


def test_validation_ledger_cli_json_and_text() -> None:
    code, text = capture([])
    assert code == 1
    assert "Issue #49 independent validation: 1/8 groups checked" in text
    assert "Closeable: no" in text
    assert "indexed_and_bit_devices" in text

    code, raw = capture(["--format", "json"])
    assert code == 1
    payload = json.loads(raw)
    assert payload["closeable"] is False
    assert payload["groups"]["outputs_and_multiple_writers"]["status"] == "checked"


def main_tests() -> None:
    test_validation_ledger_reports_issue_49_not_closeable_yet()
    test_validation_ledger_cli_json_and_text()
    print("validation-ledger checks passed")


if __name__ == "__main__":
    main_tests()
