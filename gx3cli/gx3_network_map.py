from __future__ import annotations

"""Build a compact network / safety relationship map.

This command intentionally reuses existing report products:
- comm-refresh / comm-detail CSVs for CC-Link and external source edges
- ip-map style parameter DB scan for Ethernet/IP nodes
- scon-map conventions for IAI/SCON axes
- index-lite comments for safety related devices
"""

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path

from gx3cli.gx3_cli import project_label_from_root
from gx3cli.gx3_index_lite import default_db_path
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.gx3_tools import IP_RE, open_db


SAFETY_RE = re.compile(
    r"安全|非常停止|非常|EMG|E-?STOP|ESTOP|safety|light curtain|ライトカーテン|ドア|door|interlock|インターロック",
    re.IGNORECASE,
)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({field: row.get(field, "") for field in fields})


def add_node(nodes: dict[str, dict[str, object]], node_id: str, kind: str, label: str, detail: str = "") -> None:
    rec = nodes.setdefault(node_id, {"id": node_id, "kind": kind, "label": label, "detail": detail})
    if detail and not rec.get("detail"):
        rec["detail"] = detail


def add_edge(edges: list[dict[str, object]], src: str, dst: str, kind: str, label: str, evidence: str) -> None:
    edges.append({"source": src, "target": dst, "kind": kind, "label": label, "evidence": evidence})


def collect_ip_nodes(root: Path, nodes: dict[str, dict[str, object]], edges: list[dict[str, object]], project_node: str) -> None:
    for db in sorted(root.glob("*.db")):
        if db.name.endswith(("_LDDB.db", "_MilDB.db", "_StepInfo.db", "_DC.db", "_DM.db")) or db.name == "LabelData.db":
            continue
        try:
            con = open_db(db)
        except sqlite3.Error:
            continue
        try:
            for trow in con.execute("select name from sqlite_master where type='table'"):
                table = str(trow[0])
                cols = [r[1] for r in con.execute(f'pragma table_info("{table}")')]
                if {"Label", "Data"}.issubset(cols):
                    for r in con.execute(f'select Label, Data from "{table}" where Data is not null'):
                        value = str(r["Data"])
                        for ip in IP_RE.findall(value):
                            node = f"ip:{ip}"
                            role = "gateway/subnet" if ip.startswith("255.") or ip.endswith(".254") else "ethernet-node"
                            add_node(nodes, node, "ip", ip, f"{role}; {db.name}:{table}:{r['Label']}")
                            add_edge(edges, project_node, node, "ethernet", role, db.name)
        finally:
            con.close()


def collect_comm_csvs(output_dir: Path, prefix: str, nodes: dict[str, dict[str, object]], edges: list[dict[str, object]], project_node: str) -> None:
    assignments = read_csv(output_dir / f"{prefix}_detail_remote_station_assignments.csv")
    if not assignments:
        assignments = read_csv(output_dir / f"{prefix}_comm_detail_remote_station_assignments.csv")
    for row in assignments:
        station = row.get("equipment_name") or row.get("module_name") or row.get("station_no") or row.get("object_id") or "remote_station"
        node = f"station:{station}"
        add_node(nodes, node, "cc-link-station", station, row.get("module_name", ""))
        label = row.get("rx_range") or row.get("rwr_range") or row.get("remote_rx_range") or ""
        add_edge(edges, project_node, node, "cc-link", label, row.get("evidence_file", "comm-detail"))

    external = read_csv(output_dir / f"{prefix}_detail_external_source_detail.csv")
    if not external:
        external = read_csv(output_dir / f"{prefix}_comm_detail_external_source_detail.csv")
    for row in external:
        dev = row.get("plc_device") or row.get("device") or ""
        if not dev:
            continue
        source = row.get("remote_equipment_name") or row.get("source_unit_name") or row.get("source_kind") or "external"
        node = f"external:{source}"
        add_node(nodes, node, "external-source", source, row.get("source_detail", ""))
        add_edge(edges, node, project_node, "external-input", dev, row.get("first_title", "comm-detail"))

    refresh = read_csv(output_dir / f"{prefix}_refresh_areas.csv")
    if not refresh:
        refresh = read_csv(output_dir / f"{prefix}_comm_refresh_areas.csv")
    for row in refresh:
        area = row.get("refresh_device_range") or row.get("device_range") or row.get("plc_range") or ""
        kind = row.get("area_kind") or row.get("direction") or "refresh"
        if area:
            add_edge(edges, project_node, project_node, "refresh-area", f"{kind}:{area}", row.get("unit_name", "comm-refresh"))


def collect_scon(index_db: Path, nodes: dict[str, dict[str, object]], edges: list[dict[str, object]], project_node: str) -> None:
    if not index_db.exists():
        return
    con = sqlite3.connect(index_db)
    con.row_factory = sqlite3.Row
    try:
        for prefix in ("SCON1/RB01", "SCON2/RB02", "SCON", "IAI"):
            rows = con.execute("select device, all_text from comments where all_text like ? order by device limit 200", (f"%{prefix}%",)).fetchall()
            if not rows:
                continue
            node = f"scon:{prefix}"
            add_node(nodes, node, "scon-axis", prefix, f"comment hits={len(rows)}")
            for r in rows[:20]:
                add_edge(edges, project_node, node, "scon-device", str(r["device"]), str(r["all_text"] or ""))
    finally:
        con.close()


def collect_safety(index_db: Path, nodes: dict[str, dict[str, object]], edges: list[dict[str, object]], project_node: str) -> None:
    if not index_db.exists():
        return
    con = sqlite3.connect(index_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            select device, comment, occurrences, driver_rows, condition_uses
            from devices
            where comment like '%安全%' or comment like '%非常%' or comment like '%EMG%' or
                  comment like '%STOP%' or comment like '%safety%' or comment like '%door%' or
                  comment like '%インターロック%' or comment like '%ドア%'
            order by device_type, number
            """
        ).fetchall()
        add_node(nodes, "safety:safety", "safety", "Safety / interlock", f"device hits={len(rows)}")
        for r in rows:
            add_edge(edges, "safety:safety", project_node, "safety-signal", str(r["device"]), str(r["comment"] or ""))
    finally:
        con.close()


def mermaid(nodes: dict[str, dict[str, object]], edges: list[dict[str, object]], limit: int) -> str:
    def mid(text: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", text)

    lines = ["flowchart LR"]
    for node in list(nodes.values())[:limit]:
        lines.append(f"  {mid(str(node['id']))}[\"{str(node['label']).replace(chr(34), '')}\"]")
    node_ids = set(list(nodes)[:limit])
    for edge in edges[: limit * 2]:
        if edge["source"] in node_ids and edge["target"] in node_ids:
            label = str(edge.get("label", "")).replace('"', "")
            lines.append(f"  {mid(str(edge['source']))} -- \"{label}\" --> {mid(str(edge['target']))}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate network / CC-Link / SCON / safety relationship map.")
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--index-db", default="")
    parser.add_argument("--max-mermaid-nodes", type=int, default=80)
    args = parser.parse_args(argv)

    root = Path(args.root)
    label = project_label_from_root(root)
    prefix = args.prefix or f"{label}_comm"
    output_dir = Path(args.output_dir)
    index_db = Path(args.index_db or default_db_path(root))
    project_node = f"project:{label}"
    nodes: dict[str, dict[str, object]] = {}
    edges: list[dict[str, object]] = []
    add_node(nodes, project_node, "project", label, str(root))

    collect_ip_nodes(root, nodes, edges, project_node)
    collect_comm_csvs(output_dir, prefix, nodes, edges, project_node)
    collect_scon(index_db, nodes, edges, project_node)
    collect_safety(index_db, nodes, edges, project_node)

    out_prefix = output_dir / f"{label}_network_map"
    node_rows = list(nodes.values())
    write_csv(out_prefix.with_name(out_prefix.name + "_nodes.csv"), node_rows, ["id", "kind", "label", "detail"])
    write_csv(out_prefix.with_name(out_prefix.name + "_edges.csv"), edges, ["source", "target", "kind", "label", "evidence"])
    md = out_prefix.with_suffix(".md")
    md.write_text(
        "\n".join(
            [
                f"# {label} Network / Safety Map",
                "",
                f"- Nodes: {len(node_rows)}",
                f"- Edges: {len(edges)}",
                "",
                "```mermaid",
                mermaid(nodes, edges, args.max_mermaid_nodes),
                "```",
                "",
                f"- Node CSV: `{out_prefix.name}_nodes.csv`",
                f"- Edge CSV: `{out_prefix.name}_edges.csv`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = {"root": str(root), "nodes": len(node_rows), "edges": len(edges), "markdown": str(md)}
    out_prefix.with_name(out_prefix.name + "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"network map: {md}")
    print(f"nodes={len(node_rows)} edges={len(edges)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
