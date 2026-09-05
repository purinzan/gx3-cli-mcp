from __future__ import annotations

"""Two ways a rung's operands came out wearing each other's types.

Both were found by reading 51 real projects two ways and asking where the two
readings disagree. Both are alignment failures: something in the row was not
counted the same way on both sides, and every operand after it took the
previous one's type.

A pointer operand. "CALL #P240 D13491" spells P then D in the header. The
cross-reference did not spend the P, so the pointer's number took the D and the
rung recorded a D240 the program does not have, while the D13491 it does have
was gone.

A connector element. GX writes e{s=src{n=#}} and e{s=dst{n=#}} where a rung
continues on another grid row. They carry a position and no arguments, and the
printed rung counted one as an operation -- taking the header op that belonged
to the element after it. One rung in 211300 has one, and on that rung the
printed ladder named the wrong device nine times over.
"""

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_ladder_print import parse_rung
from gx3cli.review_gx3_project import LadderRow


def ladder_row(data: str) -> LadderRow:
    return LadderRow(
        lddb="test_LDDB.db", pos=1, block_id="1", title="", blocktype=0,
        rowsize=0, data=data, dim="", operations=[], parse_status="",
    )


# M100 driving CALL #P240 D13491: a pointer operand followed by a device.
POINTER_ROW = (
    "V1:6:1:1:2:3:3:a:M:CALL:P:D:cb{fg=fg{dim=4x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}]}:args=["
    "d{s=#:a=240:vt=nn}:d{s=#:a=13491:vt=nn}]}:pos=1,0}]}}"
)

# A contact, a connector where the rung continues, then a coil. The connector
# has a position and no arguments.
CONNECTOR_ROW = (
    "V1:5:1:1:1:a:M:c:L:cb{fg=fg{dim=12x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}:"
    "e{s=src{n=#}:pos=11,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=1,0}]}}"
)


def test_a_pointer_does_not_take_the_next_operands_type() -> None:
    operations, status = parse_row_occurrences(POINTER_ROW)
    assert status == "exact", status
    call = [entry for entry in operations if entry[1] == "CALL"]
    assert len(call) == 1, operations
    devices = [occ.device for occ in call[0][2]]

    # The pointer is where the program calls to, not a device: it is not an
    # occurrence. What must survive is the operand after it.
    assert devices == ["D13491"], devices
    assert "D240" not in devices, devices


def test_the_printed_rung_still_spells_the_pointer() -> None:
    ops, _verticals, _wires = parse_rung(ladder_row(POINTER_ROW))
    call = [op for op in ops if op.role == "CALL"]
    assert len(call) == 1, [(op.role, op.operands) for op in ops]
    assert call[0].operands == ["#P240", "D13491"], call[0].operands


def test_a_connector_is_not_counted_as_an_operation() -> None:
    ops, _verticals, _wires = parse_rung(ladder_row(CONNECTOR_ROW))
    spelled = [(op.role, list(op.operands)) for op in ops]
    assert spelled == [("a", ["M100"]), ("c", ["L200"])], spelled


def test_both_readings_of_a_connector_rung_agree() -> None:
    # The check that found it: the printed rung and the cross-reference reading
    # the same row and naming the same devices.
    ops, _verticals, _wires = parse_rung(ladder_row(CONNECTOR_ROW))
    printed = {operand for op in ops for operand in op.operands}
    operations, _status = parse_row_occurrences(CONNECTOR_ROW)
    analysed = {occ.device for _r, _o, args, _c in operations for occ in args}
    assert printed == analysed == {"M100", "L200"}, (printed, analysed)


def main() -> int:
    test_a_pointer_does_not_take_the_next_operands_type()
    test_the_printed_rung_still_spells_the_pointer()
    test_a_connector_is_not_counted_as_an_operation()
    test_both_readings_of_a_connector_rung_agree()
    print("operand alignment checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
