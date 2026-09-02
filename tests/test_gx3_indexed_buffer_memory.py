from __future__ import annotations

r"""Buffer memory carrying an index register (U96\G196608Z0).

Its header token is "Zs", the same token a plain indexed device uses. The
buffer branch never consumed it, so the token was still there when the next
operand asked for its device type: a BMOV whose third operand is D48200Z2
decoded, and printed, as an index register Z48200. The D occurrence was then
missing from the cross-reference and a device that cannot exist -- an index
register numbered 48200 -- was in it instead.

The row below is the shape taken from a real project, with no project data in
it beyond the operand structure.
"""

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_ladder_print import parse_rung
from gx3cli.review_gx3_project import LadderRow


ROW = (
    "V1:13:1:1:1:1:4:1:2:3:2:1:2:1:2:a:X:a:X:BMOV:D:Zs:Ats:Us:G:Zs:D:Zs:cb{fg=fg{dim=6x1:es=[e{s=ce{op=ct"
    "{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=2400:vt=nn}]}:pos=0,0}:e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Ab"
    "l}]}:args=[d{s=#:a=2416:vt=nn}]}:pos=1,0}:e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16"
    "}]}:args=[M{b=M{b=d{s=#:a=48350:vt=nn}:m=d{s=#:a=1:vt=nn}}:m=c{s=#:v=0}}:M{b=B{b=d{s=#:a=150:vt=nn}:"
    "e=d{s=#:a=196608:vt=nn}:vt=i}:m=d{s=#:a=0:vt=nn}}:M{b=d{s=#:a=48200:vt=nn}:m=d{s=#:a=2:vt=nn}}]}:pos"
    "=2,0}]}}"
)


def ladder_row() -> LadderRow:
    return LadderRow(
        lddb="test_LDDB.db",
        pos=96256,
        block_id="1",
        title="",
        blocktype=0,
        rowsize=0,
        data=ROW,
        dim="",
        operations=[],
        parse_status="",
    )


def test_indexed_buffer_memory_does_not_steal_the_next_operand() -> None:
    parsed, status = parse_row_occurrences(ROW)
    assert status == "exact", status
    bmov = [entry for entry in parsed if entry[1] == "BMOV"]
    assert len(bmov) == 1, parsed
    devices = [(occ.device, occ.arg_index) for occ in bmov[0][2]]

    assert ("D48200", 2) in devices, devices
    assert not any(dev.startswith("Z48200") for dev, _ in devices), devices
    assert ("U96\\G196608Z0", 1) in devices, devices


def test_printed_operands_spell_the_rung_as_gx_does() -> None:
    ops, _verticals, _wires = parse_rung(ladder_row())
    bmov = [op for op in ops if op.role == "BMOV"]
    assert len(bmov) == 1, [(op.role, op.operands) for op in ops]
    assert bmov[0].operands == ["D48350Z1", "U96\\G196608Z0", "D48200Z2"], bmov[0].operands


def main() -> int:
    test_indexed_buffer_memory_does_not_steal_the_next_operand()
    test_printed_operands_spell_the_rung_as_gx_does()
    print("indexed buffer memory checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
