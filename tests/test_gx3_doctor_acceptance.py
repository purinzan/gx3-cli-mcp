from __future__ import annotations

import contextlib
import io
import json

from gx3cli.gx3_doctor_acceptance import ACCEPTANCE, main, summarize


def capture(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(args)
    return int(code or 0), buf.getvalue()


def test_doctor_acceptance_is_closeable() -> None:
    summary = summarize()
    assert summary["issue"] == 135
    assert summary["checked_items"] == len(ACCEPTANCE)
    assert summary["required_items"] == 12
    assert summary["closeable"] is True
    assert summary["missing_items"] == []


def test_doctor_acceptance_cli_text_and_json() -> None:
    code, text = capture([])
    assert code == 0
    assert "Issue #135 Doctor acceptance: 12/12 items checked" in text
    assert "Closeable: yes" in text

    code, raw = capture(["--format", "json"])
    assert code == 0
    payload = json.loads(raw)
    assert payload["closeable"] is True
    assert len(payload["items"]) == 12


def main_tests() -> None:
    test_doctor_acceptance_is_closeable()
    test_doctor_acceptance_cli_text_and_json()
    print("doctor acceptance checks passed")


if __name__ == "__main__":
    main_tests()
