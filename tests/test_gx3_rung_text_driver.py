from __future__ import annotations

"""rung-text names the device a rung drives, so it has to be the one written.

A rung printed as

    [D+   D32706   D37426   D32706 ]

reads D32706 and D37426 and writes D32706. rung-text reported it as driving
D37426, a source. Two things put it there:

- One device can be several operands of one instruction. The device list
  dropped the repeat, keeping the first reference -- the read -- so the element
  looked like it wrote nothing.
- With nothing written, the fallback asked the manuals which operand is the
  destination, passing the number of devices. The write positions are operand
  positions, and a collapsed list or a constant operand makes those two
  numbers differ: for a three-operand D+ it asked about the two-operand form
  and got the second operand, which D+ reads and writes only in that form.

Measured over one real project: 17 of 15736 driven-device decisions named a
device the decoder says is not written there, all of them arithmetic.
"""

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_ladder_logic import positioned_elements
from gx3cli.gx3_rung_text import written_devices
from gx3cli.review_gx3_project import LadderRow


# M50716 driving D+ D32706 D37426 D32706, the shape taken from a real rung.
ROW = (
    "V1:9:1:1:4:1:1:1:1:a:M:D+:D:D:D:cb{fg=fg{dim=6x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=50716:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A32}:as{vt=A32}:as{vt=A32}]}:args=["
    "d{s=#:a=32706:vt=nn}:d{s=#:a=37426:vt=nn}:d{s=#:a=32706:vt=nn}]}:pos=1,0}]}}"
)

# The same instruction with a constant operand: MOV K5 D100 has two operands
# and one device.
CONST_ROW = (
    "V1:6:1:1:4:1:1:a:M:MOV:K_1:D:cb{fg=fg{dim=4x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=1:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}]}:args=["
    "c{s=#:v=5}:d{s=#:a=100:vt=nn}]}:pos=1,0}]}}"
)


def ladder_row(data: str) -> LadderRow:
    return LadderRow(
        lddb="test_LDDB.db", pos=100352, block_id="1", title="", blocktype=0,
        rowsize=0, data=data, dim="", operations=[], parse_status="",
    )


def instruction_element(data: str, opcode: str):
    elements = [e for e in positioned_elements(ladder_row(data)) if e.opcode == opcode]
    assert len(elements) == 1, [(e.opcode, e.role) for e in elements]
    return elements[0]


def test_a_repeated_device_keeps_the_write() -> None:
    element = instruction_element(ROW, "D+")
    by_device = {ref.device: ref for ref in element.devices}
    assert set(by_device) == {"D32706", "D37426"}, by_device
    # D32706 is operand one and operand three: read and written.
    assert by_device["D32706"].access == "both", by_device["D32706"].access
    assert by_device["D32706"].is_written
    assert not by_device["D37426"].is_written


def test_the_driven_device_is_the_one_the_rung_writes() -> None:
    element = instruction_element(ROW, "D+")
    assert written_devices(element) == ["D32706"], written_devices(element)

    # And the decoder agrees, on the same row.
    operations, status = parse_row_occurrences(ROW)
    assert status == "exact", status
    written = {
        occ.device
        for _role, opcode, args, _c in operations
        if opcode == "D+"
        for occ in args
        if occ.access in ("write", "both")
    }
    assert written == {"D32706"}, written


def test_an_operand_count_is_not_a_device_count() -> None:
    # MOV K5 D100: two operands, one device. Asking the manuals with the
    # device count would ask about a one-operand MOV, which they do not have.
    element = instruction_element(CONST_ROW, "MOV")
    assert element.argc == 2, element.argc
    assert written_devices(element) == ["D100"], written_devices(element)


def main() -> int:
    test_a_repeated_device_keeps_the_write()
    test_the_driven_device_is_the_one_the_rung_writes()
    test_an_operand_count_is_not_a_device_count()
    print("rung-text driver checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
