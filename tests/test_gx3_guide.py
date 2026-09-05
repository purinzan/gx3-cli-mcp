from __future__ import annotations

"""Which commands to run on this project, and every command being findable.

There are sixty-odd commands. `list` prints all of them and `--help` grouped
them, but neither said which ones would find anything in the project at hand:
a project with no communication units has nothing for comm-detail, and one
written with labels needs label-probe where a device-based one does not.

The help groups were also a hand-written list of five categories naming 25 of
the commands, which had fallen behind COMMANDS -- the other 37 were reachable
only through `list`, and nothing in the help said they existed.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_cli import COMMANDS, COMMAND_CATEGORIES, command_group_lines
from gx3cli.gx3_guide import gather, render, suggest, to_json
from gx3cli.gx3_synthetic_project import create_demo_line_project


def _commands(root: Path) -> list[str]:
    return [suggestion.command for suggestion in suggest(gather(root))]


def test_a_ladder_project_is_told_to_build_the_cross_reference_first() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "demo"
        create_demo_line_project(root)
        commands = _commands(root)
        assert commands[0] == "doctor"
        # Most commands read the cross-reference, so it comes before them.
        assert commands.index("workspace --prepare") < commands.index("lint")
        assert "metrics" in commands
        assert "rung-text" in commands


def test_a_label_project_is_pointed_at_the_label_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a_LDDB.db").write_bytes(b"")
        sqlite3.connect(root / "LabelData.db").close()
        commands = _commands(root)
        assert "label-probe" in commands
        reason = next(s.reason for s in suggest(gather(root)) if s.command == "label-probe")
        assert "label" in reason.lower()


def test_what_is_absent_is_not_suggested() -> None:
    # The point of reading the project first: a suggestion nothing supports is
    # worse than no suggestion, because it sends someone to an empty result.
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a_LDDB.db").write_bytes(b"")
        commands = _commands(root)
        for absent in ("label-probe", "dm-probe", "mildb-probe", "motion-rd77", "gtx-probe"):
            assert absent not in commands, absent


def test_a_container_with_no_ladder_says_so() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        commands = _commands(Path(tmp))
        assert "inspect" in commands
        assert "lint" not in commands


def test_every_suggestion_carries_its_reason() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "demo"
        create_demo_line_project(root)
        for suggestion in suggest(gather(root)):
            assert suggestion.reason.strip(), suggestion.command
        # And the rendering shows both.
        text = "\n".join(render(gather(root), suggest(gather(root))))
        assert "start with" in text
        assert "cross-reference" in text


def test_json_reports_the_evidence_as_well_as_the_advice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "demo"
        create_demo_line_project(root)
        payload = to_json(gather(root), suggest(gather(root)))
        assert set(payload) == {"root", "evidence", "suggestions"}
        assert payload["evidence"]["ladder_programs"] > 0


def test_the_help_lists_every_command() -> None:
    # Generated from COMMANDS, so a command cannot be added without appearing.
    listed = " ".join(command_group_lines())
    missing = [name for name in COMMANDS if name not in listed]
    assert not missing, f"not shown in --help: {sorted(missing)}"


def test_every_command_has_a_group() -> None:
    known = {name for name, _description in COMMAND_CATEGORIES}
    ungrouped = [name for name, spec in COMMANDS.items() if spec.category not in known]
    assert not ungrouped, f"no group: {sorted(ungrouped)}"


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} guide checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
