from __future__ import annotations

"""Regression tests for gx3_lint check logic.

The math checks are exercised with hand-built RowOp lists injected into the
context's op cache, so they do not depend on any extracted project. The
row-operation decoder is also checked against a generated rung to prove the
real parse path stays aligned.
"""

import sqlite3
from pathlib import Path

from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_lint import (
    CHECKS,
    LintContext,
    OpArg,
    RowOp,
    check_div_by_zero,
    check_comment_conflict,
    check_multi_writer,
    check_signed_compare,
    check_width_mismatch,
    const_int,
    decode_row_ops,
)
from gx3cli.review_gx3_project import LadderRow


def make_row(lddb: str, pos: int) -> LadderRow:
    return LadderRow(
        lddb=lddb,
        pos=pos,
        block_id="{x}",
        title="t",
        blocktype=0,
        rowsize=1,
        data="",
        dim="",
        operations=[],
        parse_status="exact",
    )


def ctx_with(ops_by_row: list[tuple[LadderRow, list[RowOp]]]) -> LintContext:
    rows = [row for row, _ in ops_by_row]
    ctx = LintContext(root=Path("."), rows=rows, comments={})
    for row, ops in ops_by_row:
        ctx.row_ops_cache[f"{row.lddb}:{row.pos}"] = ops
    return ctx


def dev(index: int, name: str, dtype: str, num: int, access: str) -> OpArg:
    return OpArg(index=index, kind="device", device=name, device_type=dtype, number=num, access=access)


def const(index: int, value: str) -> OpArg:
    return OpArg(index=index, kind="const", const=value)


def xref_ctx(records: list[dict[str, object]]) -> LintContext:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        create table xref (
            device text, device_type text, number integer, access text,
            role text, opcode text, arg_index integer, const_args text,
            detail text, lddb text, pos integer, pou text, step integer,
            title text, comment text, parse_status text
        )
        """
    )
    for i, rec in enumerate(records):
        row = {
            "device": rec.get("device", "D100"),
            "device_type": rec.get("device_type", "D"),
            "number": rec.get("number", 100),
            "access": rec.get("access", "write"),
            "role": rec.get("role", rec.get("opcode", "MOV")),
            "opcode": rec.get("opcode", rec.get("role", "MOV")),
            "arg_index": rec.get("arg_index", 1),
            "const_args": rec.get("const_args", ""),
            "detail": rec.get("detail", ""),
            "lddb": rec.get("lddb", f"A{i}"),
            "pos": rec.get("pos", i * 1024),
            "pou": rec.get("pou", f"P{i}"),
            "step": rec.get("step", i),
            "title": rec.get("title", ""),
            "comment": rec.get("comment", ""),
            "parse_status": rec.get("parse_status", "exact"),
        }
        con.execute(
            """
            insert into xref values (
                :device, :device_type, :number, :access, :role, :opcode,
                :arg_index, :const_args, :detail, :lddb, :pos, :pou,
                :step, :title, :comment, :parse_status
            )
            """,
            row,
        )
    con.commit()
    return LintContext(root=Path("."), rows=[], comments={}, xref=con)


def lite_ctx_without_ladder_rows() -> LintContext:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table comments (device text, device_type text, number integer, all_text text)")
    con.executemany(
        "insert into comments values (?, ?, ?, ?)",
        [("M100", "M", 100, "duplicate label"), ("M101", "M", 101, "duplicate label")],
    )
    con.commit()
    return LintContext(root=Path("."), rows=[], comments={}, lite=con)


def test_registry_has_all_checks() -> None:
    for name in ["duplicate-coil", "multi-writer", "div-by-zero", "width-mismatch", "signed-compare"]:
        assert name in CHECKS, f"missing check: {name}"


def test_comment_conflict_skips_without_ladder_rows() -> None:
    assert check_comment_conflict(lite_ctx_without_ladder_rows()) == []


def test_const_int() -> None:
    assert const_int("0") == 0
    assert const_int("100") == 100
    assert const_int("-5") == -5
    assert const_int("99999999") == 99999999
    assert const_int("abc") is None


def test_div_by_zero_constant() -> None:
    row = make_row("A", 0)
    # / S1 S2 D  with S2 = constant 0
    op = RowOp(role="/", opcode="/", base="/", args=[dev(0, "D10", "D", 10, "read"), const(1, "0"), dev(2, "D20", "D", 20, "write")])
    findings = check_div_by_zero(ctx_with([(row, [op])]))
    assert len(findings) == 1, findings
    assert findings[0]["severity"] == "high"


def test_div_by_zero_nonzero_ok() -> None:
    row = make_row("A", 0)
    op = RowOp(role="/", opcode="/", base="/", args=[dev(0, "D10", "D", 10, "read"), const(1, "3"), dev(2, "D20", "D", 20, "write")])
    # xref is None so "no writer" path is not triggered; nonzero constant => no finding
    assert check_div_by_zero(ctx_with([(row, [op])])) == []


def test_multi_writer_reset_pou_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1000", "number": 1000, "opcode": "RST__16", "role": "RST__16", "pou": "P_RESET", "pos": 0},
                {"device": "D1000", "number": 1000, "opcode": "MOV", "role": "MOV", "pou": "P_OWNER", "pos": 1024},
                {"device": "D1000", "number": 1000, "opcode": "MOV", "role": "MOV", "pou": "P_OWNER", "pos": 2048},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "nonreset_POUs=1" in findings[0]["detail"]


def test_multi_writer_scratch_multiple_pous_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D400", "number": 400, "pou": "P_CALC_A", "pos": 0, "comment": "scratch calculation buffer"},
                {"device": "D400", "number": 400, "pou": "P_CALC_B", "pos": 1024, "comment": "scratch calculation buffer"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "scratch" in findings[0]["detail"]


def test_multi_writer_indexed_multiple_pous_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "ZR100", "device_type": "ZR", "number": 100, "pou": "P_INDEX_A", "pos": 0, "detail": "Z1 indexed"},
                {"device": "ZR100", "device_type": "ZR", "number": 100, "pou": "P_INDEX_B", "pos": 1024, "detail": "Z1 indexed"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "indexed-address" in findings[0]["detail"]


def test_multi_writer_process_data_multiple_pous_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1200", "number": 1200, "pou": "P_PROCESS_A", "pos": 0, "comment": "process recipe value"},
                {"device": "D1200", "number": 1200, "pou": "P_PROCESS_B", "pos": 1024, "comment": "process recipe value"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "process-data" in findings[0]["detail"]


def test_multi_writer_product_payload_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1300", "number": 1300, "pou": "P_PAYLOAD_A", "pos": 0, "comment": "product payload id"},
                {"device": "D1300", "number": 1300, "pou": "P_PAYLOAD_B", "pos": 1024, "comment": "product payload id"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "process-data" in findings[0]["detail"]


def test_multi_writer_scratch_register_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1400", "number": 1400, "pou": "P_SCRATCH_A", "pos": 0, "comment": "scratch working register"},
                {"device": "D1400", "number": 1400, "pou": "P_SCRATCH_B", "pos": 1024, "comment": "scratch working register"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "scratch" in findings[0]["detail"]


def test_multi_writer_hmi_display_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1500", "number": 1500, "pou": "P_HMI_A", "pos": 0, "comment": "hmi display page"},
                {"device": "D1500", "number": 1500, "pou": "P_HMI_B", "pos": 1024, "comment": "hmi display page"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "hmi-display" in findings[0]["detail"]


def test_multi_writer_counter_stats_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1600", "number": 1600, "pou": "P_COUNT_A", "pos": 0, "comment": "counter history value"},
                {"device": "D1600", "number": 1600, "pou": "P_COUNT_B", "pos": 1024, "comment": "counter history value"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "counter-history" in findings[0]["detail"]


def test_multi_writer_measurement_value_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D1700", "number": 1700, "pou": "P_MEASURE", "pos": 0, "comment": "measurement value"},
                {"device": "D1700", "number": 1700, "pou": "P_RESET", "pos": 1024, "comment": "measurement value"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "process-data" in findings[0]["detail"]


def test_multi_writer_buffer_device_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "UG100", "device_type": "UG", "number": 100, "pou": "P_BUFFER_A", "pos": 0, "title": "buffer record"},
                {"device": "UG100", "device_type": "UG", "number": 100, "pou": "P_BUFFER_B", "pos": 1024, "title": "buffer record"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "buffer-record" in findings[0]["detail"]


def test_multi_writer_interface_data_multiple_pous_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "ZR200", "device_type": "ZR", "number": 200, "pou": "P_IF_A", "pos": 0, "title": "interface input"},
                {"device": "ZR200", "device_type": "ZR", "number": 200, "pou": "P_IF_B", "pos": 1024, "title": "state update"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "interface-data" in findings[0]["detail"]


def test_multi_writer_zr_file_register_range_is_medium() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "ZR300", "device_type": "ZR", "number": 300, "pou": "P_FILE_A", "pos": 0, "opcode": "FMOV"},
                {"device": "ZR300", "device_type": "ZR", "number": 300, "pou": "P_FILE_B", "pos": 1024, "opcode": "DMOV"},
            ]
        )
    )
    assert findings[0]["severity"] == "medium"
    assert "file-register-range" in findings[0]["detail"]


def test_multi_writer_multiple_nonreset_owners_is_high() -> None:
    findings = check_multi_writer(
        xref_ctx(
            [
                {"device": "D9000", "number": 9000, "pou": "P1", "pos": 0, "comment": "state word"},
                {"device": "D9000", "number": 9000, "pou": "P2", "pos": 1024, "comment": "state word"},
            ]
        )
    )
    assert findings[0]["severity"] == "high"
    assert "nonreset_POUs=2" in findings[0]["detail"]


def test_width_mismatch_high_word_reuse() -> None:
    r1 = make_row("A", 0)
    r2 = make_row("A", 1024)
    # DMOV src D100 -> dest D200 (occupies D200,D201)
    dmov = RowOp(role="DMOV", opcode="DMOV", base="DMOV", args=[dev(0, "D100", "D", 100, "read"), dev(1, "D200", "D", 200, "write")])
    # MOV writes D201 (the high word of the 32-bit destination)
    mov = RowOp(role="MOV", opcode="MOV", base="MOV", args=[dev(0, "D50", "D", 50, "read"), dev(1, "D201", "D", 201, "write")])
    findings = check_width_mismatch(ctx_with([(r1, [dmov]), (r2, [mov])]))
    assert len(findings) == 1, findings
    assert findings[0]["device"] == "D201"
    assert findings[0]["severity"] == "medium"


def test_width_mismatch_no_conflict() -> None:
    r1 = make_row("A", 0)
    r2 = make_row("A", 1024)
    dmov = RowOp(role="DMOV", opcode="DMOV", base="DMOV", args=[dev(0, "D100", "D", 100, "read"), dev(1, "D200", "D", 200, "write")])
    mov = RowOp(role="MOV", opcode="MOV", base="MOV", args=[dev(0, "D50", "D", 50, "read"), dev(1, "D300", "D", 300, "write")])
    assert check_width_mismatch(ctx_with([(r1, [dmov]), (r2, [mov])])) == []


def test_signed_compare_out_of_range_constant() -> None:
    row = make_row("A", 0)
    op = RowOp(role="<", opcode="<", base="<", args=[dev(0, "D10", "D", 10, "read"), const(1, "99999999")])
    findings = check_signed_compare(ctx_with([(row, [op])]))
    assert len(findings) == 1, findings
    assert findings[0]["severity"] == "info"


def test_signed_compare_in_range_ok() -> None:
    row = make_row("A", 0)
    op = RowOp(role="<", opcode="<", base="<", args=[dev(0, "D10", "D", 10, "read"), const(1, "100")])
    assert check_signed_compare(ctx_with([(row, [op])])) == []


def test_decode_row_ops_on_generated_rung() -> None:
    data, _rowsize, _ops = generate_rung({"device": "M100"}, {"type": "coil", "device": "M200"})
    ops = decode_row_ops(data)
    roles = [op.role for op in ops]
    assert roles == ["a", "c"], roles
    numbers = [a.number for op in ops for a in op.args if a.kind == "device"]
    assert 100 in numbers and 200 in numbers, numbers


def main() -> int:
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"ok: {name}")
    print("all gx3_lint tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
