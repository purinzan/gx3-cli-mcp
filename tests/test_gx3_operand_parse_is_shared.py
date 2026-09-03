from __future__ import annotations

"""The printed rung and the cross-reference read one row the same way.

Both walk the header type tokens against the element values, and that walk was
written twice. The copies drifted the way copies do: buffer memory carrying an
index register consumed no "Zs" token, so the next operand read it as its own
device type -- and because the bug sat in both, a rung printed D48200Z2 as an
index register Z48200 and the cross-reference recorded one, each needing its
own fix.

The walk now lives in gx3_operand_parse. What the two callers build from it
still differs, and should: the printed rung folds the modifier into one name,
the cross-reference splits the device from the index register it reads.
"""

import ast
import pathlib

from gx3cli.gx3_arg_decode import decode_args, parse_row_operations
from gx3cli.gx3_ladder_print import display_operands


ROOT = pathlib.Path(__file__).resolve().parents[1]
WALKERS = ("gx3cli/gx3_arg_decode.py", "gx3cli/gx3_ladder_print.py")

# The header spells the operand kinds; the element carries the values. One
# device with an index register, one digit-specified device, one bit of a word,
# one buffer memory with an index register, one constant.
ARG_TOKENS = ["D", "Zs", "M", "Ks", "D", "Dots", "Ats", "Us", "G", "Zs", "K_1"]
RAW_ARGS = [
    "M{b=d{s=#:a=100:vt=nn}:m=d{s=#:a=2:vt=nn}}",
    "M{b=d{s=#:a=35001:vt=nn}:m=c{s=#:v=4}}",
    "M{b=d{s=#:a=200:vt=nn}:m=c{s=#:v=5}}",
    "M{b=B{b=d{s=#:a=150:vt=nn}:e=d{s=#:a=196608:vt=nn}:vt=i}:m=d{s=#:a=0:vt=nn}}",
    "c{s=#:v=7}",
]


def test_neither_caller_keeps_its_own_walk() -> None:
    # The walk is the part that has to stay single: a token consumed in one
    # copy and not the other is a bug in one output only, and invisible from
    # the other.
    offenders: list[str] = []
    for name in WALKERS:
        path = ROOT / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in {
                "take_type",
                "take_if",
                "take_if_const",
                "skip_to_meaningful",
            }:
                offenders.append(f"{path.name}:{node.lineno} {node.name}()")
    assert not offenders, (
        "these walk the header tokens themselves; call parse_operands() from "
        "gx3_operand_parse:\n  " + "\n  ".join(offenders)
    )


def test_both_callers_see_the_same_operands() -> None:
    printed = display_operands(RAW_ARGS, ARG_TOKENS)
    assert printed == ["D100Z2", "K4M35001", "D200.5", "U96\\G196608Z0", "K7"], printed

    occurrences = decode_args(RAW_ARGS, ARG_TOKENS, "MOV")
    devices = [(occ.device, occ.detail) for occ in occurrences]
    assert devices == [
        ("D100", "Z2 indexed"),
        ("Z2", "index register"),
        ("M35001", "digit=K4"),
        ("D200", "bit=K5"),
        ("U96\\G196608Z0", "unit=0x96 Z0 indexed"),
        ("Z0", "index register"),
    ], devices


def test_the_index_register_is_never_lost_between_operands() -> None:
    # The failure this whole module exists to prevent: an operand taking the
    # token that belonged to the one before it. Every device after the buffer
    # memory has to still be itself.
    printed = display_operands(RAW_ARGS + ["d{s=#:a=48200:vt=nn}"], ARG_TOKENS + ["D"])
    assert printed[-1] == "D48200", printed


def test_row_operation_view_keeps_arg_count_and_constants() -> None:
    row = (
        "V1:6:1:2:4:1:1:1:a:SM:FMOV:K_1:D:K_1:cb{fg=fg{dim=2x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
        "c{s=#:v=0}:d{s=#:a=200:vt=nn}:c{s=#:v=10}]}:pos=1,0}]}}"
    )
    operations, status = parse_row_operations(row)
    assert status == "exact", status
    assert [(op.role, op.opcode, op.argc, op.constant_values) for op in operations] == [
        ("a", "", 1, {}),
        ("FMOV", "FMOV", 3, {0: "0", 2: "10"}),
    ]


def main() -> int:
    test_neither_caller_keeps_its_own_walk()
    test_both_callers_see_the_same_operands()
    test_the_index_register_is_never_lost_between_operands()
    test_row_operation_view_keeps_arg_count_and_constants()
    print("shared operand walk checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
