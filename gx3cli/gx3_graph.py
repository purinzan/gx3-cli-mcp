from __future__ import annotations

"""Unified graph entry point for GX Works3 project structure and device flow."""

import argparse
import json
import re
import sys
from pathlib import Path

from gx3cli.gx3_dependency_flow import build_flow, format_markdown as flow_markdown, format_mermaid as flow_mermaid
from gx3cli.gx3_format import build_format_inventory
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_flow_db import flow_xref_db
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root


NODE_RE = re.compile(r"[^0-9A-Za-z_]")


def node_id(prefix: str, value: str) -> str:
    clean = NODE_RE.sub("_", value).strip("_")
    return f"{prefix}_{clean or 'item'}"


def build_structure(root: Path) -> dict[str, object]:
    inventory = build_format_inventory(root)
    pm = load_program_map(root)
    lddbs = sorted(root.glob("*_LDDB.db"))
    programs: dict[str, list[dict[str, str]]] = {}
    unassigned: list[dict[str, str]] = []
    for lddb in lddbs:
        hexid = lddb.name.split("_")[0]
        info = pm.pous.get(hexid)
        item = {
            "lddb": lddb.name,
            "label": pm.label(lddb.name),
            "pou_dir": info.pou_dir if info else "",
        }
        program_file = info.program_file if info else ""
        if program_file:
            programs.setdefault(program_file, []).append(item)
        else:
            unassigned.append(item)
    return {
        "root": str(root),
        "format_inventory": inventory.as_dict(),
        "program_files": pm.program_files,
        "programs": programs,
        "unassigned_pous": unassigned,
        "warnings": pm.warnings,
    }


def format_structure_markdown(structure: dict[str, object]) -> str:
    lines = [
        "# GX3 Structure Graph",
        "",
        f"- root: `{structure['root']}`",
        f"- formats: {format_inventory_detail(structure)}",
    ]
    programs = structure.get("programs", {})
    if isinstance(programs, dict) and programs:
        lines.extend(["", "## Program Files"])
        for name, pous in programs.items():
            lines.append(f"- `{name}`")
            for pou in pous:
                lines.append(f"  - `{pou['label']}` ({pou['lddb']})")
    unassigned = structure.get("unassigned_pous", [])
    if isinstance(unassigned, list) and unassigned:
        lines.extend(["", "## Unassigned LDDB"])
        for pou in unassigned:
            lines.append(f"- `{pou['label']}` ({pou['lddb']})")
    warnings = structure.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {w}" for w in warnings)
    return "\n".join(lines)


def format_inventory_detail(structure: dict[str, object]) -> str:
    inventory = structure.get("format_inventory", {})
    if not isinstance(inventory, dict):
        return "unknown"
    parts = [f"{name}={count}" for name, count in inventory.items() if isinstance(count, int) and count]
    return ", ".join(parts) if parts else "no known GX3 DB files"


def format_structure_mermaid(structure: dict[str, object]) -> str:
    lines = ["flowchart TD", "  root[GX3 Project]"]
    inventory_id = "format_inventory"
    lines.append(f"  root --> {inventory_id}[{format_inventory_detail(structure)}]")
    programs = structure.get("programs", {})
    if isinstance(programs, dict):
        for name, pous in programs.items():
            pid = node_id("program", name)
            lines.append(f"  root --> {pid}[Program: {escape_label(name)}]")
            for pou in pous:
                label = str(pou.get("label", "POU"))
                lddb = str(pou.get("lddb", ""))
                lines.append(f"  {pid} --> {node_id('pou', lddb)}[POU: {escape_label(label)}]")
    unassigned = structure.get("unassigned_pous", [])
    if isinstance(unassigned, list):
        for pou in unassigned:
            label = str(pou.get("label", "POU"))
            lddb = str(pou.get("lddb", ""))
            lines.append(f"  root --> {node_id('pou', lddb)}[POU: {escape_label(label)}]")
    return "\n".join(lines)


def escape_label(text: str) -> str:
    return text.replace("[", "(").replace("]", ")").replace('"', "'")


def output_text(text: str, output: str) -> None:
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate GX3 structure and device-flow graphs.")
    parser.add_argument("--root", default=str(default_project_root()), help="GX3 archive or extracted project folder")
    parser.add_argument("--type", choices=["structure", "device-flow"], default="structure")
    parser.add_argument("--device", help="target device for --type device-flow")
    parser.add_argument("--format", choices=["markdown", "mermaid", "json"], default="markdown")
    parser.add_argument("--max-devices", type=int, default=2000, help="maximum traced devices for device-flow")
    parser.add_argument("--exclude-reset", action="store_true", help="ignore RST rows as drivers for device-flow")
    parser.add_argument("--expand-bit-groups", action="store_true", help="expand K<n>M/K<n>L bit groups for device-flow")
    parser.add_argument("--xref-db", help="cross-reference DB, for value-flow edges; found automatically when not given")
    parser.add_argument("--label-width", type=int, default=48)
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = resolve_project_root(args.root)

    if args.type == "device-flow":
        if not args.device:
            raise SystemExit("--device is required for --type device-flow")
        flow = build_flow(
            root=root,
            target_device=args.device,
            max_devices=args.max_devices,
            include_reset=not args.exclude_reset,
            expand_bit_groups=args.expand_bit_groups,
            xref_db=flow_xref_db(args, root),
        )
        if args.format == "json":
            text = json.dumps(flow, ensure_ascii=False, indent=2)
        elif args.format == "mermaid":
            text = flow_mermaid(flow, label_width=args.label_width)
        else:
            text = flow_markdown(flow, label_width=args.label_width)
        output_text(text, args.output)
        return 0

    structure = build_structure(root)
    if args.format == "json":
        text = json.dumps(structure, ensure_ascii=False, indent=2)
    elif args.format == "mermaid":
        text = format_structure_mermaid(structure)
    else:
        text = format_structure_markdown(structure)
    output_text(text, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
