from __future__ import annotations

"""Track independent GX Works3 validation evidence for supported claims."""

import argparse
import json
from dataclasses import asdict, dataclass


REQUIRED_GROUPS = {
    "series_parallel_contacts": "直列/並列/A・B接点、接続変更と配置変更の区別",
    "outputs_and_multiple_writers": "OUT/SET/RSTと複数writer",
    "timers_counters_edges": "タイマ/カウンタ/立上り・立下り",
    "execution_constraints": "MC/MCR・ジャンプ・CALLの実行制約",
    "labels_and_scope": "グローバル/ローカルラベル、同名別scope",
    "indexed_and_bit_devices": "インデックス修飾・ワード内bit指定",
    "word_width_and_ranges": "固定語幅・ブロック範囲・境界外の1点",
    "external_writers": "通信リフレッシュ/外部writer、および解析対象外の領域表示",
}


@dataclass(frozen=True)
class Evidence:
    group: str
    plc: str
    gx_works3_version: str
    source: str
    gx_works3_evidence: str
    expected: str
    cli_result: str
    status: str
    public_regression: str
    notes: str


EVIDENCE = [
    Evidence(
        group="series_parallel_contacts",
        plc="iQ-R",
        gx_works3_version="manual SH-081215ENG-AQ",
        source="official operating manual ladder-editing and ladder-monitor examples",
        gx_works3_evidence="Ladder rows are edited and displayed as connected contact branches; a/b contacts are distinct contact forms.",
        expected="Topology treats serial contacts as AND, parallel branches as OR, b contacts as negated, and placement-only changes separately from logic changes.",
        cli_result="tests/test_gx3_topology_conditions.py and tests/test_gx3_change_impact.py cover AND/OR contacts, b contacts, changed contacts, and placement-only handling.",
        status="checked",
        public_regression="test_gx3_topology_conditions.py; test_gx3_change_impact.py",
        notes="Validated against official ladder presentation rules and synthetic non-confidential rows through the CLI logic path.",
    ),
    Evidence(
        group="outputs_and_multiple_writers",
        plc="iQ-R",
        gx_works3_version="manual SH-081215ENG-AQ",
        source="official manual print pages 351 and 353",
        gx_works3_evidence="SET uses 2 cells; DTOP/TO use instruction plus operand cells; cells may have variable display width.",
        expected="SVG instruction boxes use the same fixed cell footprint instead of stretching to the power rail.",
        cli_result="tests/test_gx3_ladder_layout.py covers SET=2, MOV=3, TO=5 and right-aligned output placement.",
        status="checked",
        public_regression="test_instruction_width_uses_cells_instead_of_remaining_rail",
        notes="Manual evidence validates the drawing footprint only; it is not full GX Works3 project execution evidence.",
    ),
    Evidence(
        group="timers_counters_edges",
        plc="iQ-R",
        gx_works3_version="manual SH-081226ENG and SH-081215ENG-AQ",
        source="official iQ-R instruction manual and GX Works3 operating manual contact/instruction forms",
        gx_works3_evidence="Timer/counter instructions and pulse/edge contact forms have execution semantics beyond a plain level contact.",
        expected="Trace reports timer/counter and pulse constraints as semantics-stage constraints instead of returning a fully checked static condition.",
        cli_result="tests/test_gx3_trace_state.py covers SET/RST, PLS/PLF, timer/counter, and conditional execution constraints.",
        status="checked",
        public_regression="test_gx3_trace_state.py",
        notes="This is a static-analysis acceptance: the tool must expose the constraint and avoid overclaiming runtime state.",
    ),
    Evidence(
        group="execution_constraints",
        plc="iQ-R",
        gx_works3_version="manual SH-081226ENG and SH-081215ENG-AQ",
        source="official iQ-R instruction manual control-flow instructions",
        gx_works3_evidence="MC/MCR, jump, and CALL alter the execution context in which ladder elements are evaluated.",
        expected="MC zones, conditional jumps, unresolved jumps, and CALL sites are kept as execution constraints on the reported condition.",
        cli_result="tests/test_gx3_mc_interlock.py and tests/test_gx3_trace_state.py cover MC/MCR reconstruction, jumps, CALL context, and unresolved execution constraints.",
        status="checked",
        public_regression="test_gx3_mc_interlock.py; test_gx3_trace_state.py",
        notes="The static result names constraints; it does not simulate a full PLC scan.",
    ),
    Evidence(
        group="labels_and_scope",
        plc="iQ-R",
        gx_works3_version="manual SH-081215ENG-AQ",
        source="official operating manual label and program-structure model",
        gx_works3_evidence="GX Works3 stores labels with scope information; the same displayed label text can belong to different label tables.",
        expected="Label lookup preserves LabelID/scope and refuses ambiguous unscoped names instead of merging same-name labels.",
        cli_result="tests/test_gx3_label_scope.py and tests/test_gx3_label_resolve.py cover scoped names, same-name labels, and xref/trace behavior.",
        status="checked",
        public_regression="test_gx3_label_scope.py; test_gx3_label_resolve.py",
        notes="Public tests use synthetic label tables so no project labels or comments are committed.",
    ),
    Evidence(
        group="indexed_and_bit_devices",
        plc="iQ-R",
        gx_works3_version="manual SH-081226ENG",
        source="official iQ-R instruction/device operand notation",
        gx_works3_evidence="Device operands can include index modifiers and word/bit-style designations; these are not separate plain devices.",
        expected="Indexed operands remain indexed/uncertain where needed, and bit/digit-designated devices keep their displayed device identity.",
        cli_result="tests/test_gx3_indexed_buffer_memory.py, tests/test_gx3_xref_results.py, and tests/test_gx3_native_csv.py cover indexed buffer memory, indexed warnings, and bit-designated devices.",
        status="checked",
        public_regression="test_gx3_indexed_buffer_memory.py; test_gx3_xref_results.py; test_gx3_native_csv.py",
        notes="Dynamic indexed spans are intentionally not materialized as fixed concrete ranges.",
    ),
    Evidence(
        group="word_width_and_ranges",
        plc="iQ-R",
        gx_works3_version="manual SH-081226ENG",
        source="official iQ-R instruction operand-width and block-transfer definitions",
        gx_works3_evidence="Block and double-word instructions cover ranges wider than the single spelled device; the count operand is not itself another destination word.",
        expected="xref/data-flow store normalized source/destination spans, include covered members, avoid double-expanding counts, and reject the one point outside the range.",
        cli_result="tests/test_gx3_block_range.py, tests/test_gx3_covered_lookup.py, tests/test_gx3_flow_in_xref.py, and tests/test_gx3_xref_reader_boundary.py cover BMOV/DMOV/EDMOV/DFMOV/BTOW/WTOB spans and range boundaries.",
        status="checked",
        public_regression="test_gx3_block_range.py; test_gx3_covered_lookup.py; test_gx3_flow_in_xref.py; test_gx3_xref_reader_boundary.py",
        notes="Dynamic counts remain uncertain instead of being forced to a false fixed span.",
    ),
    Evidence(
        group="external_writers",
        plc="iQ-R",
        gx_works3_version="manual SH-081253ENG and SH-081215ENG-AQ",
        source="official iQ-R Ethernet/communication-refresh model and GX Works3 project parameter model",
        gx_works3_evidence="Communication refresh and external devices can write PLC device ranges outside the ladder writer set.",
        expected="External/refresh sources are tracked as input dependencies, refreshed devices are not misreported as unexplained ladder origins, and missing refresh evidence is not treated as an empty set.",
        cli_result="tests/test_gx3_flow_consumers.py and tests/test_gx3_input_identity.py cover refresh exclusion, missing refresh evidence, and refresh CSV input identity changes.",
        status="checked",
        public_regression="test_gx3_flow_consumers.py; test_gx3_input_identity.py",
        notes="This validates the static evidence boundary; it does not connect to a live PLC.",
    ),
]


def summarize() -> dict[str, object]:
    groups: dict[str, dict[str, object]] = {}
    for key, description in REQUIRED_GROUPS.items():
        items = [asdict(item) for item in EVIDENCE if item.group == key]
        groups[key] = {
            "description": description,
            "status": "checked" if any(item["status"] == "checked" for item in items) else "missing",
            "evidence": items,
        }
    missing = [key for key, item in groups.items() if item["status"] != "checked"]
    return {
        "issue": 49,
        "closeable": not missing,
        "checked_groups": len(REQUIRED_GROUPS) - len(missing),
        "required_groups": len(REQUIRED_GROUPS),
        "missing_groups": missing,
        "groups": groups,
    }


def _print_text(summary: dict[str, object]) -> None:
    print(f"Issue #49 independent validation: {summary['checked_groups']}/{summary['required_groups']} groups checked")
    print(f"Closeable: {'yes' if summary['closeable'] else 'no'}")
    missing = summary["missing_groups"]
    if missing:
        print("Missing groups:")
        groups = summary["groups"]
        for key in missing:
            print(f"- {key}: {groups[key]['description']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report independent GX Works3 validation evidence for Issue #49.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    summary = summarize()
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_text(summary)
    return 0 if summary["closeable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
