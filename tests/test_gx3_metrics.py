from __future__ import annotations

"""Where to start reading an unfamiliar project.

Sixty-odd commands all want to be told where to look, and nothing answered
that. metrics does: how big each program is, and which rungs carry the most
logic.

Complexity here is the number of independent paths through a rung -- one, plus
one for every parallel branch. That is cyclomatic complexity counted the way
ladder is drawn. Ladder has no "lines of code" to count: a rung is the unit an
engineer reads, and one wide rung is not the same as several narrow ones.
"""

import tempfile
from pathlib import Path

from gx3cli.gx3_metrics import (
    branch_count,
    complexity,
    condition_terms,
    hotspots,
    program_metrics,
    to_json,
)
from gx3cli.gx3_rung_text import RungText, collect
from gx3cli.gx3_synthetic_project import create_demo_line_project


def _fixture(tmp: str) -> Path:
    root = Path(tmp) / "demo"
    create_demo_line_project(root)
    return root


def _rung(condition: str, device: str = "M1", pos: int = 1, lddb: str = "a.db") -> RungText:
    return RungText(lddb=lddb, pos=pos, title="", opcode="", device=device, condition=condition)


def test_a_rung_with_no_branch_is_one_path() -> None:
    assert complexity("X10 AND M2") == 1
    assert branch_count("X10 AND M2") == 0


def test_every_parallel_branch_adds_a_path() -> None:
    assert complexity("(A AND B) OR (C AND D)") == 2
    assert complexity("A OR B OR C") == 3


def test_terms_count_devices_not_operators() -> None:
    assert condition_terms("X10 AND M2") == 2
    assert condition_terms("/X10 AND M2 AND /M100") == 3
    # Labels are terms too; a label-based project has to measure the same.
    assert condition_terms("(IN_Start OR Start_Latch) AND /IN_Stop") == 3
    assert condition_terms("TRUE") == 0
    assert condition_terms("?") == 0


def test_programs_are_summarised_and_ordered_by_size() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        programs = program_metrics(collect(_fixture(tmp)))
        assert programs
        # Biggest first, so the list itself is the reading order.
        assert programs == sorted(programs, key=lambda m: -m.rungs)
        assert all(m.rungs > 0 and m.outputs > 0 for m in programs)
        assert all(m.max_complexity >= 1 for m in programs)


def test_hotspots_put_the_most_branching_rung_first() -> None:
    items = [
        _rung("A", pos=1),
        _rung("(A AND B) OR (C AND D) OR E", pos=2),
        _rung("A AND B", pos=3),
    ]
    top = hotspots(items, 3)
    assert top[0].pos == 2
    assert complexity(top[0].condition) == 3


def test_unreadable_rungs_are_reported_rather_than_dropped() -> None:
    # A rung whose condition could not be read still counts as a rung. A
    # complexity figure that quietly left it out would overstate how well the
    # project is understood.
    programs = program_metrics([_rung("A AND B", pos=1), _rung("?", pos=2)])
    assert programs[0].rungs == 2
    assert programs[0].unresolved == 1


def test_json_carries_the_three_sections() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        items = collect(_fixture(tmp))
        payload = to_json(program_metrics(items), hotspots(items, 3), {})
        assert set(payload) == {"project", "programs", "hotspots"}
        assert payload["project"]["rungs"] == sum(p["rungs"] for p in payload["programs"])
        assert len(payload["hotspots"]) == 3


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} metrics checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
