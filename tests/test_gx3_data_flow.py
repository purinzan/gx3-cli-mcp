from __future__ import annotations

"""Regression tests for conservative argument-level value flow."""

from gx3cli.gx3_arg_decode import ArgOcc
from gx3cli.gx3_data_flow import records_for_operation, transfer_count


def occ(device: str, arg_index: int, access: str, device_type: str = "D") -> ArgOcc:
    number = int(device[len(device_type) :])
    return ArgOcc(device, device_type, number, access, arg_index)


def test_mov_creates_one_source_to_destination_edge() -> None:
    records = records_for_operation(
        "MOV",
        2,
        [occ("D100", 0, "read"), occ("D200", 1, "write")],
    )
    assert len(records) == 1
    edge = records[0]
    assert edge.record_kind == "edge"
    assert edge.source_device == "D100"
    assert edge.destination_device == "D200"
    assert edge.source_arg_index == 0
    assert edge.destination_arg_index == 1
    assert edge.confidence == "manual"
    assert edge.source_range == "D100"
    assert edge.destination_range == "D200"


def test_bmov_preserves_count_and_ranges() -> None:
    records = records_for_operation(
        "BMOV",
        3,
        [occ("D100", 0, "read"), occ("D200", 1, "write")],
        const_args="2",
    )
    assert len(records) == 1
    edge = records[0]
    assert edge.range_count == 2
    assert edge.source_range == "D100..D101"
    assert edge.destination_range == "D200..D201"
    assert edge.source_word_width == 1
    assert edge.destination_word_width == 1


def test_block_transfer_uses_the_positional_count_operand() -> None:
    records = records_for_operation(
        "FMOV",
        3,
        [occ("D200", 1, "write")],
        const_args="0,10",
        constant_values={0: "0", 2: "10"},
    )
    # There is no device source for a constant fill, so this is intentionally
    # unresolved rather than a guessed edge. The count logic is tested through
    # the public helper below.
    assert records == []
    assert transfer_count("FMOV", "0,10", {0: "0", 2: "10"}) == 10


def test_dmov_reports_two_word_ranges() -> None:
    records = records_for_operation(
        "DMOV",
        2,
        [occ("D100", 0, "read"), occ("D200", 1, "write")],
    )
    assert len(records) == 1
    edge = records[0]
    assert edge.source_word_width == 2
    assert edge.destination_word_width == 2
    assert edge.source_range == "D100..D101"
    assert edge.destination_range == "D200..D201"


def test_two_operand_arithmetic_keeps_read_modify_write_edge() -> None:
    records = records_for_operation(
        "+",
        2,
        [occ("D100", 0, "read"), occ("D200", 1, "both")],
    )
    assert {(record.source_device, record.destination_device) for record in records} == {
        ("D100", "D200"),
        ("D200", "D200"),
    }
    assert all(record.read_modify_write for record in records)


def test_unknown_and_partial_operations_are_unresolved_not_edges() -> None:
    unknown = records_for_operation(
        "NOT_A_REAL_INSTRUCTION",
        2,
        [occ("D100", 0, "read"), occ("D200", 1, "write")],
    )
    partial = records_for_operation(
        "MOV",
        2,
        [occ("D100", 0, "read"), occ("D200", 1, "write")],
        parse_status="partial",
    )
    assert len(unknown) == len(partial) == 1
    assert unknown[0].record_kind == partial[0].record_kind == "unresolved"
    assert unknown[0].confidence == "unknown"
    assert partial[0].parse_status == "partial"


def main() -> int:
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    for test in tests:
        test()
    print(f"{len(tests)} checks passed in test_gx3_data_flow.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
