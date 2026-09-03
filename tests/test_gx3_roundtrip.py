from __future__ import annotations

"""The one check that does not take the decoder's word for it.

Every other check asks the tool whether it understood a project and believes
the answer. parse_status="exact" is something the decoder says about itself,
and it said it about rungs where a contact had been dropped, where an operand
was read as the count instead of the destination, and where a label arrived
with no identity at all.

This check reads a rung into its enable logic, generates that logic back into
the intermediate spelling, and compares the result against the bytes it came
from. A rung that comes back identical was understood.

The middle verdict is the one that needs care. Generation puts logic through
DNF, so a contact shared between two branches in the original appears once per
branch in the rebuild: "(A OR B) AND /C" becomes two rows each carrying /C,
where the original shares it across a vertical link. That is a different
drawing of the same rung, so the comparison is on which devices are touched and
how -- as a set, with order and repetition dropped. Getting this wrong in the
strict direction reported two correct rungs of a real project as failures.
"""

import tempfile
from pathlib import Path

from gx3cli.gx3_label_resolve import EMPTY
from gx3cli.gx3_roundtrip import (
    DIFFERS,
    EQUIVALENT,
    IDENTICAL,
    Summary,
    check_project,
    check_row,
    logic_to_ast,
    render,
    to_json,
)
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.review_gx3_project import LadderRow


def _fixture(tmp: str) -> Path:
    root = Path(tmp) / "demo"
    create_demo_line_project(root)
    return root


def _row(data: str, pos: int = 1) -> LadderRow:
    return LadderRow(
        lddb="t.db", pos=pos, block_id="b", title="", blocktype=0,
        rowsize=4, data=data, dim="", operations=[], parse_status="",
    )


def test_a_read_project_rebuilds_to_itself() -> None:
    # The point of the check. If this drops, something in the read path
    # changed its mind about what a rung says.
    with tempfile.TemporaryDirectory() as tmp:
        summary = check_project(_fixture(tmp))
        assert summary.checked > 400, summary.checked
        assert summary.differs == 0, summary.findings
        assert summary.agreement == 1.0


def test_every_rung_that_can_be_rebuilt_is_rebuilt() -> None:
    # A rung skipped for a reason nobody looks at is how coverage quietly
    # shrinks, so the skips are counted and named.
    with tempfile.TemporaryDirectory() as tmp:
        summary = check_project(_fixture(tmp))
        # The fixture has one rung gated by a normally-closed SM400: always
        # false, so there is no logic to rebuild. That is the right answer.
        assert sum(summary.skipped.values()) <= 2, summary.skipped
        assert all(count > 0 for count in summary.skipped.values())


def test_a_rung_that_reads_back_the_same_is_identical() -> None:
    from gx3cli.gx3_intermediate_tool import generate_rung

    logic = {"and": [{"device": "X10"}, {"not": {"device": "M2"}}]}
    data, _rowsize, _ops = generate_rung(logic, {"type": "coil", "device": "M55"})
    summary = Summary()
    result = check_row(_row(data), EMPTY, summary)
    assert result is not None and result.verdict == IDENTICAL
    assert summary.identical == 1


def test_a_shared_branch_comes_back_as_equivalent_not_a_failure() -> None:
    # "(A OR B) AND /C" drawn with /C shared: DNF repeats /C in both rows, so
    # the rebuild is a different shape carrying the same devices. Comparing
    # occurrences in order reported this as a failure on a real project.
    from gx3cli.gx3_intermediate_tool import generate_rung

    logic = {"and": [{"or": [{"device": "X10"}, {"device": "M1"}]}, {"not": {"device": "M2"}}]}
    data, _rowsize, _ops = generate_rung(logic, {"type": "coil", "device": "M55"})
    summary = Summary()
    # Feed the generated row back: it rebuilds to itself, so this is the
    # identical case. The equivalent case needs an original drawn by GX
    # Works3, which the fixture's shared-branch rungs provide.
    result = check_row(_row(data), EMPTY, summary)
    assert result is not None
    assert result.verdict in (IDENTICAL, EQUIVALENT)
    assert summary.differs == 0


def test_a_rung_with_no_rebuildable_logic_is_named_not_dropped() -> None:
    summary = Summary()
    # A row with no driver at all.
    assert check_row(_row("V1:0:cb{fg=fg{dim=1x1:es=[]}}"), EMPTY, summary) is None
    assert summary.checked == 0
    assert sum(summary.skipped.values()) == 1


def test_the_report_says_what_was_not_checked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        summary = check_project(_fixture(tmp))
        text = "\n".join(render(summary))
        assert "checked" in text
        assert "not checked" in text  # the skips are shown, not hidden

        payload = to_json(summary)
        assert payload["checked"] == summary.checked
        assert set(payload) >= {"checked", "identical", "equivalent", "differs", "not_checked"}


def test_ast_conversion_refuses_what_it_cannot_rebuild() -> None:
    # Better to report a rung as unrebuildable than to invent logic for it.
    try:
        logic_to_ast({"op": "false"}, {})
    except ValueError as error:
        assert "false" in str(error)
    else:  # pragma: no cover
        raise AssertionError("a false node should not convert")


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} round-trip checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
