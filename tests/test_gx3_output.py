from __future__ import annotations

"""One way to ask any command for JSON.

Sixty-odd commands grew their own answer to "how do I get this as JSON".
Seventeen took `--json` as a flag, fourteen took `--format json`, and which one
a command wanted was something you found out by reading its help, or by it
failing. That is small on its own and large across sixty commands, because the
caller it costs most is the one that cannot read help text: an agent driving
the CLI had to know, per command, which spelling to use.

`--format` is the spelling now. `--json` still works everywhere it already did,
because removing it would break callers, and the two land in the same place.
"""

import argparse
import json
import pathlib
import re
import tempfile

from gx3cli.gx3_cli import COMMANDS
from gx3cli.gx3_output import (
    JSON,
    TEXT,
    add_format_alias,
    add_format_argument,
    chosen_format,
    emit,
    fold_format_alias,
    render_json,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_format_argument(parser)
    return parser.parse_args(argv)


def test_format_and_the_json_shorthand_mean_the_same_thing() -> None:
    assert chosen_format(_parse([])) == TEXT
    assert chosen_format(_parse(["--format", "json"])) == JSON
    assert chosen_format(_parse(["--json"])) == JSON


def test_the_alias_folds_into_the_flag_a_command_already_reads() -> None:
    # The seventeen commands whose output is a flag keep reading args.json;
    # they should not have to be rewritten to accept the one spelling.
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    add_format_alias(parser)

    assert fold_format_alias(parser.parse_args(["--format", "json"])).json is True
    assert fold_format_alias(parser.parse_args(["--json"])).json is True
    assert fold_format_alias(parser.parse_args([])).json is False


def test_emit_writes_the_asked_for_format() -> None:
    args = _parse(["--format", "json"])
    payload = {"a": 1, "日本語": "そのまま"}
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "x.json"
        args.output = str(out)
        assert emit(args, text=lambda: ["ignored"], data=lambda: payload) == 0
        assert json.loads(out.read_text(encoding="utf-8")) == payload


def test_emit_does_not_build_what_was_not_asked_for() -> None:
    # The renderers are callables so an expensive one is not run for nothing.
    built: list[str] = []
    args = _parse(["--format", "json"])
    args.output = ""
    emit(args, text=lambda: built.append("text") or "", data=lambda: {"ok": True})
    assert built == []


def test_a_format_a_command_cannot_produce_is_refused_not_guessed() -> None:
    args = _parse([])
    args.output = ""
    # No text renderer given: better to say so than to print JSON instead.
    assert emit(args, data=lambda: {"a": 1}) == 2


def test_json_keeps_utf8_as_it_is() -> None:
    assert "日本語" in render_json({"k": "日本語"})


def test_no_command_takes_json_without_also_taking_format() -> None:
    # The point of the change: one spelling reaches every command that has
    # JSON at all. A command that adds --json on its own would put the caller
    # back to checking per command.
    offenders: list[str] = []
    for name, spec in sorted(COMMANDS.items()):
        path = ROOT / "gx3cli" / spec.script
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if 'add_argument("--json"' not in source:
            continue
        if 'add_argument("--format"' in source or "add_format_alias" in source or "add_format_argument" in source:
            continue
        offenders.append(f"{name} ({spec.script})")
    assert not offenders, (
        "these take --json but not --format; add add_format_alias()/"
        "fold_format_alias() from gx3_output:\n  " + "\n  ".join(sorted(set(offenders)))
    )


def test_json_is_spelled_one_way() -> None:
    # ensure_ascii=False and indent=2 everywhere, so output does not change
    # shape depending on which command produced it.
    bad: list[str] = []
    for path in sorted((ROOT / "gx3cli").glob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        for call in re.findall(r"json\.dumps\(([^;]{0,200}?)\)\n", source):
            if "ensure_ascii=False" not in call and "indent" in call:
                bad.append(f"{path.name}: {call.strip()[:60]}")
    assert not bad, "json.dumps without ensure_ascii=False:\n  " + "\n  ".join(bad)


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} output-format checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
