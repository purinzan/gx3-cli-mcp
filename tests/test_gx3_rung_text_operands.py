from __future__ import annotations

"""Invented LD rows exercise the decoder, display, and CLI together."""

import csv
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from gx3cli.gx3_arg_decode import parse_row_operations
from gx3cli.gx3_label_resolve import LabelRef, LabelResolver
from gx3cli.gx3_rung_text import collect, collect_csv, rung_texts, to_json
from gx3cli.gx3_synthetic_project import create_synthetic_project
from gx3cli.review_gx3_project import LadderRow


def device(number: int) -> str:
    return f"d{{s=#:a={number}:vt=nn}}"


def constant(number: int) -> str:
    return f"c{{s=#:v={number}}}"


def instruction_row(opcode: str, tokens: list[str], args: list[str]) -> LadderRow:
    types = ":".join("as{vt=A16}" for _ in args)
    header = ":".join([opcode, *tokens])
    data = (
        f"V1:6:1:1:1:1:a:X:{header}:cb{{fg=fg{{dim=3x1:es=["
        f"e{{s=ce{{op=ct{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{device(0)}]}}:pos=0,0}}:"
        "e{s=wire:pos=1,0}:"
        f"e{{s=ce{{op=in{{op=#:ct=a:as=[{types}]}}:args=[{':'.join(args)}]}}:pos=2,0}}]}}}}"
    )
    return LadderRow(lddb="001_LDDB.db", pos=1, block_id="synthetic", title="",
                     blocktype=0, rowsize=6, data=data, dim="3x1", operations=[], parse_status="")


def comparison_row(tokens: list[str], args: list[str]) -> LadderRow:
    data = (
        f"V1:6:1:1:1:1:>=:{':'.join(tokens)}:c:Y:cb{{fg=fg{{dim=4x1:es=["
        f"e{{s=ce{{op=ct{{op=#:ct=a:as=[as{{vt=A16}}:as{{vt=A16}}]}}:args=[{':'.join(args)}]}}:pos=0,0}}:"
        f"e{{s=ce{{op=cl{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{device(0)}]}}:pos=3,0}}]}}}}"
    )
    return LadderRow(lddb="001_LDDB.db", pos=1, block_id="synthetic", title="",
                     blocktype=0, rowsize=6, data=data, dim="4x1", operations=[], parse_status="")


def create_fixture(root: Path) -> Path:
    create_synthetic_project(root)
    rows = [
        instruction_row("OUT__16", ["T", "K_1"], [device(0), constant(100)]),
        instruction_row("MOV", ["D", "D"], [device(0), device(10)]),
        instruction_row("FROM", ["K_1", "K_1", "D", "K_1"], [constant(0), constant(1), device(100), constant(2)]),
        instruction_row("TO", ["K_1", "K_1", "D", "K_1"], [constant(0), constant(1), device(100), constant(2)]),
        instruction_row("SYNTH_UNSUPPORTED", ["D", "D"], [device(0), device(10)]),
        comparison_row(["D", "K_1"], [device(100), constant(10)]),
    ]
    with closing(sqlite3.connect(root / "001_LDDB.db")) as con:
        con.execute("delete from LadderBlocks")
        for index, row in enumerate(rows, 1):
            con.execute("insert into LadderBlocks values (?, ?, 0, ?, ?, 0, 0)",
                        (f"_guid/00000000-0000-0000-0000-{index:012d}", index, row.data, row.rowsize))
        con.commit()
    return root


def test_timer_and_data_operands_keep_order_and_write_target() -> None:
    cases = [
        ("OUT__16", ["T", "K_1"], [device(0), constant(100)], "OUT", "T0", ("T0", "K100")),
        ("OUT__16", ["T", "D"], [device(0), device(100)], "OUT", "T0", ("T0", "D100")),
        ("OUTH__16", ["T", "D"], [device(1), device(100)], "OUTH", "T1", ("T1", "D100")),
        ("OUT__16", ["ST", "K_1"], [device(2), constant(30)], "OUT", "ST2", ("ST2", "K30")),
        ("OUT__16", ["T", "H_1"], [device(3), constant(100)], "OUT", "T3", ("T3", "H64")),
        ("MOV", ["D", "D"], [device(0), device(10)], "MOV", "D10", ("D0", "D10")),
        ("DMOV", ["D", "D"], [device(0), device(20)], "DMOV", "D20", ("D0", "D20")),
        ("BMOV", ["D", "D", "K_1"], [device(0), device(10), constant(4)], "BMOV", "D10", ("D0", "D10", "K4")),
        ("FMOV", ["K_1", "D", "K_1"], [constant(0), device(20), constant(8)], "FMOV", "D20", ("K0", "D20", "K8")),
        ("+", ["D", "K_1", "D"], [device(0), constant(5), device(20)], "+", "D20", ("D0", "K5", "D20")),
        ("CMP", ["D", "K_1", "M"], [device(0), constant(10), device(20)], "CMP", "M20", ("D0", "K10", "M20")),
        ("MOV", ["D", "D"], [device(100), device(100)], "MOV", "D100", ("D100", "D100")),
    ]
    for opcode, tokens, args, displayed, target, operands in cases:
        items = rung_texts(instruction_row(opcode, tokens, args))
        assert len(items) == 1
        item = items[0]
        assert (item.opcode, item.device, item.condition) == (displayed, target, "X0")
        assert item.operands == operands
        assert item.to_line().endswith(f"{displayed} {' '.join(operands)}")
        assert to_json(items)[0]["operands"] == list(operands)


def test_comparison_conditions_keep_both_operand_orders() -> None:
    for tokens, args, expected in [
        (["D", "K_1"], [device(100), constant(10)], ">= D100 K10"),
        (["K_1", "D"], [constant(10), device(100)], ">= K10 D100"),
        (["D", "D"], [device(100), device(100)], ">= D100 D100"),
    ]:
        item = rung_texts(comparison_row(tokens, args))[0]
        assert item.device == "Y0" and item.condition == expected


def test_timer_labels_are_recognized_without_inventing_values() -> None:
    row = instruction_row("OUT__16", ["_lid/synthetic/1", "_lid/synthetic/2"], ["l{id=#}", "l{id=#}"])
    labels = LabelResolver({("synthetic", 1): LabelRef("Delay_Timer"), ("synthetic", 2): LabelRef("Delay_Preset")})
    operations, status = parse_row_operations(row.data, labels)
    assert status == "exact" and [op.role for op in operations] == ["a", "OUT__16"]
    item = rung_texts(row, labels)[0]
    assert item.device == "Delay_Timer"
    assert item.operands == ("Delay_Timer", "Delay_Preset")
    unresolved = rung_texts(row)[0]
    assert unresolved.operands == ("?", "?") and unresolved.diagnostic
    for opcode, tokens, args, expected in [
        ("OUT__16", ["T", "_lid/synthetic/2"], [device(0), "l{id=#}"], ("T0", "Delay_Preset")),
        ("OUTH__16", ["_lid/synthetic/1", "D"], ["l{id=#}", device(100)], ("Delay_Timer", "D100")),
    ]:
        mixed = instruction_row(opcode, tokens, args)
        assert parse_row_operations(mixed.data, labels)[1] == "exact"
        assert rung_texts(mixed, labels)[0].operands == expected


def test_from_and_to_keep_all_operands_without_a_fake_to_destination() -> None:
    tokens, args = ["K_1", "K_1", "D", "K_1"], [constant(0), constant(1), device(100), constant(2)]
    incoming = rung_texts(instruction_row("FROM", tokens, args))[0]
    outgoing = rung_texts(instruction_row("TO", tokens, args))[0]
    assert incoming.device == "D100"
    assert outgoing.device == "" and outgoing.condition == "X0"
    assert incoming.operands == outgoing.operands == ("K0", "K1", "D100", "K2")
    assert outgoing.to_line().endswith("TO K0 K1 D100 K2")


def test_multiple_write_targets_keep_the_same_complete_instruction() -> None:
    items = rung_texts(instruction_row("XCH", ["D", "D"], [device(0), device(10)]))
    assert [item.device for item in items] == ["D0", "D10"]
    assert all(item.operands == ("D0", "D10") for item in items)


def test_unsupported_instructions_preserve_raw_arguments_and_diagnostics() -> None:
    for tokens, args in [([], []), (["D", "D"], [device(0), device(10)]), (["K_1"], ["opaque{v=100}"])]:
        item = rung_texts(instruction_row("SYNTH_UNSUPPORTED", tokens, args))[0]
        record = to_json([item])[0]
        assert item.device == "" and "unsupported" in item.diagnostic
        assert "diagnostic" in item.to_line() and "SYNTH_UNSUPPORTED" in item.to_line()
        assert record["raw_args"] == args and record["arg_tokens"] == tokens


def test_partial_header_decode_is_visible_as_raw_data() -> None:
    row = instruction_row("synthetic.custom", ["D", "D"], [device(0), device(10)])
    assert parse_row_operations(row.data)[1] == "partial"
    items = rung_texts(row)
    assert len(items) == 1 and items[0].device == ""
    record = to_json(items)[0]
    assert record["condition"] == "?" and "partial" in record["diagnostic"]
    assert record["raw_data"] == row.data
    missing_position = instruction_row("MOV", ["D", "D"], [device(0), device(10)])
    missing_position.data = missing_position.data.replace("pos=2,0", "pos=unavailable")
    record = to_json(rung_texts(missing_position))[0]
    assert record["condition"] == "?" and record["raw_data"] == missing_position.data


def test_unsupported_elements_mark_other_outputs_as_uncertain() -> None:
    for kind in ["in", "ct"]:
        row = instruction_row("SYNTH_UNSUPPORTED", ["D"], [device(0)])
        row.data = row.data.replace("dim=3x1", "dim=6x1").replace("op=in{", f"op={kind}{{")
        row.data = row.data.replace(":cb{", ":c:Y:cb{").replace(
            "]}}", f":e{{s=ce{{op=cl{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{device(0)}]}}:pos=5,0}}]}}}}", 1)
        row.dim = "6x1"
        assert parse_row_operations(row.data)[1] == "exact"
        items = rung_texts(row)
        assert [item.opcode for item in items] == ["SYNTH_UNSUPPORTED", ""]
        assert items[1].device == "Y0" and items[1].condition.startswith("?")
        assert "unsupported" in items[1].diagnostic


def test_cli_and_internal_csv_preserve_coverage_and_device_filter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = create_fixture(Path(tmp) / "fixture")
        items = collect(root)
        assert len(items) == 6
        selected = collect(root, device="D10")
        assert [item.opcode for item in selected] == ["MOV", "SYNTH_UNSUPPORTED"]
        command = [sys.executable, "-m", "gx3cli.gx3_cli", "rung-text", "--root", str(root), "--format", "json"]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
        records = json.loads(result.stdout)
        assert len(records) == 6 and records[3]["device"] == ""
        assert records[3]["operands"] == ["K0", "K1", "D100", "K2"]
        assert "unsupported" in records[4]["diagnostic"]
        path = Path(tmp) / "rows.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["lddb", "pos", "data", "blocktype"])
            writer.writeheader()
            with closing(sqlite3.connect(root / "001_LDDB.db")) as con:
                for pos, data in con.execute("select pos,data from LadderBlocks order by pos"):
                    writer.writerow({"lddb": "001_LDDB.db", "pos": pos, "data": data, "blocktype": 0})
        csv_items = collect_csv(path)
        assert [(item.opcode, item.device, item.operands) for item in csv_items] == [
            (item.opcode, item.device, item.operands) for item in items]
        assert len(collect_csv(path, device="D10")) == 2


def test_source_operand_comments_follow_the_visible_instruction() -> None:
    row = instruction_row("MOV", ["D", "D"], [device(0), device(10)])
    items = rung_texts(row, comments={("X", 0): "start", ("D", 0): "source", ("D", 10): "destination"})
    assert items[0].operands == ("D0", "D10")
    assert items[0].comments == {"X0": "start", "D10": "destination", "D0": "source"}
    assert 'D0="source"' in items[0].to_line()


def test_listed_csv_unknowns_remain_visible_with_a_device_filter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "listed.csv"
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(["step", "instruction", "operands"])
            writer.writerows([[0, "LD", "X0"], [1, "TO", "K0 K1 D100 K2"],
                              [2, "SYNTH_UNSUPPORTED", "D0 D10"], [3, "MOV", "D0 D10"]])
        items = collect_csv(path, device="D10")
        assert [item.opcode for item in items] == ["SYNTH_UNSUPPORTED", "MOV"]
        assert all("diagnostic=" in item.to_line() for item in items)


def test_empty_and_contact_only_rows_do_not_invent_outputs() -> None:
    row = instruction_row("MOV", ["D", "D"], [device(0), device(10)])
    row.data = "V1:6:1:1:1:1:cb{fg=fg{dim=0x0:es=[]}}"
    row.dim = "0x0"
    assert rung_texts(row) == []
    row.data = (
        "V1:6:1:1:1:1:a:X:cb{fg=fg{dim=1x1:es=["
        f"e{{s=ce{{op=ct{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{device(0)}]}}:pos=0,0}}]}}}}"
    )
    row.dim = "1x1"
    assert rung_texts(row) == []
    row = comparison_row(["D", "K_1"], [device(100), constant(10)])
    item = rung_texts(row)[0]
    assert not item.operands and "operands" not in to_json([item])[0]
    assert not item.diagnostic


def main() -> int:
    checks = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for check in checks:
        check()
    print(f"{len(checks)} rung-text operand and coverage checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
