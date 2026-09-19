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
