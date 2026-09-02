from __future__ import annotations

"""The destination operand has to be the one the manual names as the destination.

write_indices() used to answer from a hand-written table. For twelve
instructions that table pointed at the wrong operand, and for most of them it
pointed at the operand count: WTOB (s)(d)(n) was recorded as writing (n), so a
cross-reference said the byte count was written and never mentioned the actual
destination. Both halves of that are wrong answers about which device a rung
writes, which is what the tool is asked for.

The manual operand tables name every operand -- (d)/(d1)/(d2) destinations,
(s*) sources, (n*) counts -- so the positions are taken from there now. These
cases are the ones where the two tables disagreed.
"""

from gx3cli.gx3_arg_decode import write_indices
from gx3cli.gx3_instruction_table import MANUAL_WRITE_ARGS, manual_write_indices


def _writes(opcode: str, argc: int) -> set[int]:
    indices, _rmw = write_indices(opcode, argc)
    assert indices is not None, f"{opcode} should be classified"
    return indices


def test_count_operand_is_not_the_destination() -> None:
    # (s)(d)(n) and (s1)(s2)(d)(n): the last operand is how many, not where.
    assert _writes("WTOB", 3) == {1}
    assert _writes("BTOW", 3) == {1}
    assert _writes("BKAND", 4) == {2}
    assert _writes("SERDATA", 4) == {2}
    assert _writes("BKRST", 2) == {0}


def test_destination_before_the_last_source() -> None:
    # These spell the destination in the middle: (s1)(d)(s2), (s1)(s2)(d)(s3).
    assert _writes("MIDR", 3) == {1}
    assert _writes("MIDW", 3) == {1}
    assert _writes("INSTR", 4) == {2}


def test_destination_first() -> None:
    # STRDEL (d)(s)(n) edits the string in place; BREAK (d)(P) stores the
    # remaining repeat count in (d) and was recorded as writing nothing.
    assert _writes("STRDEL", 3) == {0}
    assert _writes("BREAK", 2) == {0}


def test_module_instructions_report_every_destination() -> None:
    # G.INPUT (U)(s)(d1)(d2): the received data area counts as written, not
    # only the completion bit.
    assert _writes("G.INPUT", 4) == {2, 3}
    assert _writes("ZP.CSET", 5) == {3, 4}


def test_operand_count_selects_the_destination() -> None:
    # WAND with two operands writes the second, with three the third.
    assert _writes("WAND", 2) == {1}
    assert _writes("WAND", 3) == {2}


def test_pulse_variants_resolve() -> None:
    assert _writes("WTOBP", 3) == {1}
    assert _writes("MIDRP", 3) == {1}


def test_two_operand_arithmetic_stays_read_modify_write() -> None:
    # "+" with two operands reads and writes the same device. The operand table
    # cannot express that, so the arithmetic path has to keep priority.
    indices, rmw = write_indices("+", 2)
    assert indices == {1}
    assert rmw is True


def test_block_compare_records_its_result_area() -> None:
    # BKCMP/DBKCMP (s1)(s2)(d)(n) store the comparison result into (d). They
    # used to be matched by the contact-comparison regex, which answers "writes
    # nothing", so every device a block compare writes went unrecorded.
    assert _writes("BKCMP=", 4) == {2}
    assert _writes("BKCMP<>P_U", 4) == {2}
    assert _writes("DBKCMP>=", 4) == {2}


def test_contact_comparisons_write_nothing() -> None:
    # Including the unsigned and date/time variants, which used to fall through
    # as unknown so their operands were reported as "ref" rather than "read".
    for opcode, argc in (
        ("LD=", 2), ("AND<=_U", 2), ("ORD>", 2), ("LDD>", 2),
        ("ANDDT<", 3), ("ORTM=", 3), ("LDED>=", 3),
    ):
        assert _writes(opcode, argc) == set(), opcode


def test_module_dedicated_instructions_are_covered() -> None:
    # SH-081975. The manual documents several of these per page under a shared
    # heading ("J(P).READ，G(P).READ"), and names the axis-numbered variants
    # only in the ST signature block ("ENO:=G_ABRST1(EN,U,s,d);").
    assert _writes("JP.READ", 5) == {3, 4}
    assert _writes("GP.WRITE", 5) == {3, 4}
    assert _writes("G.ABRST1", 3) == {2}
    assert _writes("ZP.TEACH4", 3) == {2}
    assert _writes("G.OUTPUT", 4) == {3}


def test_division_is_not_lost_to_the_cell_separator() -> None:
    # The instruction symbol is literally "/". Reading the manual tables with
    # " / " as the separator between paragraphs inside a cell ate it, and the
    # whole division family went missing.
    assert _writes("/", 3) == {2}
    assert _writes("D/", 3) == {2}
    assert _writes("B/P", 3) == {2}


def test_instructions_with_no_st_form_are_still_covered() -> None:
    # SIN has no ST form ("SIN命令はST，FBD/LDでは対応していません"), so the ST
    # block on its page names only SINP. Letting the signatures replace the
    # heading rather than add to it dropped SIN itself.
    assert _writes("SIN", 2) == {1}
    assert _writes("SINP", 2) == {1}
    assert _writes("SQRT", 2) == {1}


def test_zero_operand_instructions_are_classified() -> None:
    # They have no operands, so nothing hangs on this at decode time -- but the
    # coverage report should not call them unknown.
    for opcode in ("STOP", "COM", "DCONTSW", "END", "NOP", "ANB", "ORB", "IRET"):
        assert _writes(opcode, 0) == set(), opcode


def test_operands_named_differently_in_st_and_the_table() -> None:
    # RTMRD is (J/U)(s1)(s2)(s3)(d1)(d2) in the operand table and (J,s1,s2,s3,
    # s4,d) in ST. The names do not line up, but the count does, so the table
    # still decides.
    assert _writes("J.RTMRD", 6) == {4, 5}
    assert _writes("ZP.RTMRD", 6) == {4, 5}


def test_a_page_documenting_two_instructions_does_not_mix_them() -> None:
    # RND and SRND share a page. RND is (d) and SRND is (s); reading the
    # signatures per page rather than per section gave RND the operands of
    # SRND.
    assert _writes("RND", 1) == {0}
    assert _writes("SRND", 1) == set()


def test_unknown_opcode_still_reports_unknown() -> None:
    # Nothing in this change should make an unrecognised opcode look classified.
    assert write_indices("NOT_A_REAL_INSTRUCTION", 2) == (None, False)


def test_table_is_indexed_within_the_operand_count() -> None:
    for opcode, by_argc in MANUAL_WRITE_ARGS.items():
        for argc, indices in by_argc.items():
            assert all(0 <= i < argc for i in indices), (opcode, argc, indices)
            assert manual_write_indices(opcode, argc) == set(indices)
