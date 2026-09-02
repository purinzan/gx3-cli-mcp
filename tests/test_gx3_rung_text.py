from __future__ import annotations

"""A program has to be readable without reading the print layout.

ladder-print reproduces the GX Works3 print output, box drawing and all, which
is what you want when checking against the engineering tool. It is the wrong
shape for anything that has to read the logic: the demo-line fixture comes to
about 1 MB of box drawing at up to 280 columns, and an agent asked to reason
about an interlock has to rebuild the circuit from the rules first.

rung-text is the reading form -- one line per driven device, condition to
output. The same fixture is about 25 KB.

The expression is not derived here: it comes from the same
enable_logic_for_output() that matiec-st uses, so the two cannot disagree about
what a rung means.
"""

import tempfile
from pathlib import Path

from gx3cli.gx3_rung_text import collect, render_text, simplify, to_json
from gx3cli.gx3_synthetic_project import create_demo_line_project


def _fixture(tmp: str) -> Path:
    root = Path(tmp) / "demo"
    create_demo_line_project(root)
    return root


def test_brackets_are_dropped_but_grouping_is_kept() -> None:
    assert simplify("[X10]") == "X10"
    assert simplify("([X10] AND [M2])") == "X10 AND M2"
    assert simplify("[/X10]") == "/X10"
    # Two groups side by side: the outer parentheses are not redundant here and
    # stripping them would change what the expression says.
    assert simplify("([A] OR [B]) AND ([C] OR [D])") == "(A OR B) AND (C OR D)"


def test_a_rung_reads_as_condition_then_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        items = collect(_fixture(tmp))
        assert items, "the fixture should have rungs"

        by_device = {item.device: item for item in items}
        # X10 AND M2 drives M10 in the fixture's first program.
        assert "M10" in by_device
        assert by_device["M10"].condition == "X10 AND M2"

        line = by_device["M10"].to_line()
        assert "->" in line
        assert line.endswith("M10")


def test_the_driving_instruction_is_named_when_it_is_not_a_plain_coil() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        items = collect(_fixture(tmp))
        setters = [item for item in items if item.opcode == "SET"]
        assert setters, "the fixture drives devices with SET"
        assert setters[0].to_line().endswith(f"SET {setters[0].device}")


def test_a_data_instruction_reports_the_operand_it_writes() -> None:
    # MOV is not one of the coil-like roles, so a rung that only moves a value
    # used to produce no line at all -- a program came back missing every rung
    # that wrote a word. The manuals name the destination, so MOV D0 D10
    # reports D10 and not D0.
    from gx3cli.gx3_rung_text import rung_texts
    from gx3cli.review_gx3_project import LadderRow

    data = (
        "V1:6:1:1:1:1:a:X:MOV:D:D:cb{fg=fg{dim=2x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=16:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=in{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=0:vt=nn},"
        "d{s=#:a=10:vt=nn}]}:pos=1,0}]}}"
    )
    row = LadderRow(
        block_id="b", lddb="t.db", pos=1, title="", blocktype=0, rowsize=6,
        data=data, dim="2x1", operations=[], parse_status="",
    )
    items = rung_texts(row)
    assert [(item.opcode, item.device) for item in items] == [("MOV", "D10")]


def test_filters_narrow_the_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _fixture(tmp)
        everything = collect(root)
        one_device = collect(root, device="M10")
        assert one_device
        assert {item.device for item in one_device} == {"M10"}
        assert len(one_device) < len(everything)

        program = everything[0].lddb
        one_program = collect(root, lddb=program)
        assert {item.lddb for item in one_program} == {program}


def test_it_is_far_smaller_than_the_print_layout() -> None:
    # The point of the command. Not a tight bound -- just that the reading form
    # is an order of magnitude smaller, which is what makes it usable as
    # context.
    with tempfile.TemporaryDirectory() as tmp:
        items = collect(_fixture(tmp))
        rendered = "\n".join(render_text(items))
        assert len(rendered) < 60_000
        assert max(len(line) for line in rendered.splitlines()) < 200


def test_json_carries_the_same_fields() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        items = collect(_fixture(tmp))
        rows = to_json(items)
        assert len(rows) == len(items)
        assert set(rows[0]) == {"lddb", "pos", "title", "opcode", "device", "condition"}


def main() -> int:
    # Collected rather than listed, so a test added later cannot be left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} rung-text checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
