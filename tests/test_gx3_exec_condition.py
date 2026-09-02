from __future__ import annotations

"""Whether an instruction runs once per transition is looked up, not guessed.

is_edge_write_op() decided it from the opcode ending in "P". That was wrong in
both directions. EXP, NOP, JMP, MPP, FPOP and PSTOP end in P without being
pulse forms, so they were treated as writing once per transition. And every
unsigned pulse instruction ends in "_U" rather than "P" -- "+P_U", "MOVP_U" and
two hundred more -- so they were treated as running every scan, which is the
opposite of what they do.

The manuals draw the execution condition beside every instruction as a timing
diagram rather than writing it out. The four diagrams were identified by their
pixels: level, rising, falling, level_off, plus "常時実行" where it is stated
in text.
"""

from gx3cli.gx3_instruction_table import (
    MANUAL_EXEC_CONDITION,
    is_edge_triggered,
    manual_exec_condition,
)
from gx3cli.gx3_scan_order import is_edge_write_op


def test_pulse_forms_are_edge_triggered() -> None:
    for opcode in ("MOVP", "DMOVP", "PLS", "BMOVP"):
        assert is_edge_triggered(opcode) is True, opcode
        assert is_edge_write_op(opcode) is True, opcode


def test_unsigned_pulse_forms_are_edge_triggered() -> None:
    # These end in "_U", so the old ending-in-P test never saw them.
    for opcode in ("+P_U", "*P_U", "CMPP_U", "DABINP_U"):
        assert is_edge_triggered(opcode) is True, opcode
        assert is_edge_write_op(opcode) is True, opcode


def test_opcodes_that_merely_end_in_p_are_not() -> None:
    for opcode in ("EXP", "NOP", "JMP", "MPP", "FPOP", "PSTOP"):
        assert is_edge_triggered(opcode) is False, opcode
        assert is_edge_write_op(opcode) is False, opcode


def test_edge_contacts_are_not_edge_triggered_writes() -> None:
    # LDP/ANDP/ORP run every scan -- the contact detects the edge, the
    # instruction itself is 常時実行. They are not edge-triggered writes.
    for opcode in ("LDP", "ANDP", "ORP"):
        assert manual_exec_condition(opcode) == "always", opcode
        assert is_edge_write_op(opcode) is False, opcode


def test_falling_edge_counts_as_edge_triggered() -> None:
    assert manual_exec_condition("PLF") == "falling"
    assert is_edge_triggered("PLF") is True


def test_unknown_opcode_falls_back_instead_of_answering() -> None:
    assert is_edge_triggered("ZZ_NOT_AN_INSTRUCTION") is None
    # The caller keeps its own fallback for opcodes the manuals do not carry.
    assert is_edge_write_op("ZZ_NOT_AN_INSTRUCTIONP") is True


def test_every_condition_is_one_of_the_known_five() -> None:
    assert set(MANUAL_EXEC_CONDITION.values()) <= {
        "level",
        "rising",
        "falling",
        "level_off",
        "always",
    }
