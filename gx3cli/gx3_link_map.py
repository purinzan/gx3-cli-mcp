from __future__ import annotations

"""Build and query cross-project communication link maps.

The mapper intentionally starts conservative:
- exact shared B/W/SB/SW devices with complementary read/write usage
- expanded W ranges from BMOV/FMOV-style xref rows
- Japanese handoff signal comments with matching roles and complementary usage

The output is a neutral SQLite DB that other tools can use to point across PLC
project boundaries.
"""

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from gx3cli.gx3_xref import default_db_path, normalize_device


DEVICE_RE = re.compile(r"^([A-Z]+)(\d+)$", re.IGNORECASE)
COMM_COMMENT_RE = re.compile(
    r"(前工程|後工程|上流|下流|搬入|搬出|リンク|ﾘﾝｸ|パレット|ﾊﾟﾚｯﾄ|データ|ﾃﾞｰﾀ|"
    r"自動運転|連続運転|異常なし|非常停止|PLC RUN|工程|ready|request|complete|data|link|auto|normal|fault|run)"
)
ROLE_SYNONYMS = {
    "連続運転中": "自動運転中",
    "シャッタ開": "シャッター開",
    "シャッタ開端": "シャッター開端",
    "シャッタ閉端": "シャッター閉端",
    "ﾘﾝｸ正常確認": "リンク正常確認",
    "ﾊﾟﾚｯﾄﾃﾞｰﾀ送信": "パレットデータ送信",
    "ﾊﾟﾚｯﾄﾃﾞｰﾀ受信完了": "パレットデータ受信完了",
}
WRITE_ACCESS = {"write", "both"}
READ_ACCESS = {"read", "both"}
NETWORK_TYPES = {"B", "W", "SB", "SW"}
EQUIPMENT_NAME_RE = re.compile(r"\b[A-Z][A-Z0-9_-]{2,}\b")


@dataclass(frozen=True)
class ProjectSpec:
    label: str
    root: Path
    db: Path


@dataclass
class DeviceInfo:
    project: str
    device: str
    device_type: str
    number: int
    comment: str = ""
    has_read: bool = False
    has_write: bool = False
    evidence: set[str] | None = None
    titles: set[str] | None = None

    def __post_init__(self) -> None:
        if self.evidence is None:
            self.evidence = set()
        if self.titles is None:
            self.titles = set()

    @property
    def role(self) -> str:
        return canonical_role(self.comment)

    @property
    def communication_like(self) -> bool:
        return self.device_type in NETWORK_TYPES or bool(COMM_COMMENT_RE.search(self.comment))


@dataclass(frozen=True)
class LinkRow:
    project_a: str
    device_a: str
    project_b: str
    device_b: str
    link_type: str
    link_addr: str
    direction: str
    confidence: str
    role: str
    evidence: str


def project_label_from_root(root: Path) -> str:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"


def parse_project_spec(text: str) -> ProjectSpec:
    if "=" in text:
        label, raw_root = text.split("=", 1)
        root = Path(raw_root)
        label = label.strip() or project_label_from_root(root)
    else:
        root = Path(text)
        label = project_label_from_root(root)
    return ProjectSpec(label=label, root=root, db=default_db_path(root))


def parse_device_name(device: str) -> tuple[str, int] | None:
    match = DEVICE_RE.fullmatch(device.strip())
    if not match:
        return None
    return match.group(1).upper(), int(match.group(2))


def device_name(device_type: str, number: int) -> str:
    return f"{device_type.upper()}{number}"


def merge_info(target: DeviceInfo, row: sqlite3.Row, evidence: str, use_comment: bool = True) -> None:
    comment = str(row["comment"] or "")
    if use_comment and comment:
        target.comment = comment
    access = str(row["access"])
    if access in READ_ACCESS:
        target.has_read = True
    if access in WRITE_ACCESS:
        target.has_write = True
    target.evidence.add(evidence)
    title = str(row["title"] or "")
    if title:
        target.titles.add(title)


def const_numbers(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", text or "")]


def span_length(row: sqlite3.Row) -> int:
    op = str(row["opcode"] or row["role"] or "").upper()
    nums = const_numbers(str(row["const_args"] or ""))
    if op in {"BMOV", "BMOVP"} and nums:
        return max(1, nums[0])
    if op in {"FMOV", "FMOVP"} and nums:
        return max(1, nums[-1])
    return 1


def load_project_devices(spec: ProjectSpec) -> dict[str, DeviceInfo]:
    if not spec.db.exists():
        raise SystemExit(f"xref db not found for {spec.label}: {spec.db}")
    con = sqlite3.connect(spec.db)
    con.row_factory = sqlite3.Row
    devices: dict[str, DeviceInfo] = {}

    def ensure(device: str, device_type: str, number: int) -> DeviceInfo:
        info = devices.get(device)
        if info is None:
            info = DeviceInfo(spec.label, device, device_type, number)
            devices[device] = info
        return info

    for row in con.execute(
        """
        select id, device, device_type, number, access, role, opcode, arg_index,
               const_args, lddb, pos, title, comment
        from xref
        where access in ('read', 'write', 'both')
        order by id
        """
    ):
        device = str(row["device"])
        device_type = str(row["device_type"]).upper()
        number = int(row["number"])
        evidence = f"{row['lddb']}:{row['pos']}:{row['opcode'] or row['role']}:{row['access']}"
        merge_info(ensure(device, device_type, number), row, evidence)

        length = span_length(row)
        if device_type in {"W", "D"} and length > 1:
            for offset in range(1, length):
                expanded = device_name(device_type, number + offset)
                merge_info(ensure(expanded, device_type, number + offset), row, evidence + f"+{offset}", use_comment=False)
    con.close()
    return devices


def signal_score(text: str) -> int:
    cleaned = strip_direction_tokens(text)
    return len(cleaned)


def strip_direction_tokens(text: str) -> str:
    s = text
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"[（(][^）)]*[前後上下]工程?[^）)]*[）)]", "", s)
    s = re.sub(r"[（(](?:上流側?へ?|下流側?へ?|前工程|後工程)[）)]", "", s)
    s = re.sub(r"\b[A-Z][A-Z0-9_-]{2,}\b", "", s)
    for token in [
        "上流側へ",
        "下流側へ",
        "上流側",
        "下流側",
        "前工程より",
        "後工程より",
        "前工程",
        "後工程",
        "より",
        "へ",
        "⇒",
        "→",
        "←",
    ]:
        s = s.replace(token, "")
    return s


def canonical_role(comment: str) -> str:
    if not comment:
        return ""
    s = comment.strip()
    if "⇒" in s:
        parts = [p for p in s.split("⇒") if p]
        if parts:
            s = max(parts, key=signal_score)
    elif "→" in s:
        parts = [p for p in s.split("→") if p]
        if parts:
            s = max(parts, key=signal_score)
    s = strip_direction_tokens(s)
    s = re.sub(r"[ 　\t\r\n:：,，/／・_\-]+", "", s)
    s = s.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
    for src, dst in ROLE_SYNONYMS.items():
        s = s.replace(src, dst)
    return s


def complementary_direction(a: DeviceInfo, b: DeviceInfo) -> str:
    if a.has_write and b.has_read:
        return f"{a.project}_to_{b.project}"
    if b.has_write and a.has_read:
        return f"{b.project}_to_{a.project}"
    return ""


def add_link(rows: dict[tuple[str, str, str, str, str], LinkRow], row: LinkRow) -> None:
    key = (row.project_a, row.device_a, row.project_b, row.device_b, row.link_type)
    existing = rows.get(key)
    if existing is None:
        rows[key] = row
        return
    if len(row.evidence) > len(existing.evidence):
        rows[key] = row


CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def row_endpoint(row: LinkRow, project: str) -> str:
    if row.project_a == project:
        return row.device_a
    if row.project_b == project:
        return row.device_b
    return ""


def row_source_receiver(row: LinkRow) -> tuple[str, str, str, str] | None:
    if "_to_" not in row.direction:
        return None
    source_project, receiver_project = row.direction.split("_to_", 1)
    source_device = row_endpoint(row, source_project)
    receiver_device = row_endpoint(row, receiver_project)
    if not source_device or not receiver_device:
        return None
    return source_project, source_device, receiver_project, receiver_device


def row_number_offset(row: LinkRow) -> int | None:
    oriented = row_source_receiver(row)
    if oriented is None:
        return None
    _source_project, source_device, _receiver_project, receiver_device = oriented
    source = parse_device_name(source_device)
    receiver = parse_device_name(receiver_device)
    if source is None or receiver is None:
        return None
    return source[1] - receiver[1]


def dedupe_comment_role_links(rows: list[LinkRow]) -> list[LinkRow]:
    """Pick one best comment-role source per receiver device/role/direction.

    Comment matching can produce several same-role candidates. The true PLC
    handoff links usually sit in a contiguous device band, so after confidence
    we prefer the candidate whose source/receiver number offset matches the
    dominant band for the direction.
    """
    offset_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if row.link_type != "comment-role":
            continue
        offset = row_number_offset(row)
        if offset is not None:
            offset_counts[row.direction][offset] += 1
    dominant_offset: dict[str, int] = {}
    for direction, counts in offset_counts.items():
        offset, count = max(counts.items(), key=lambda item: item[1])
        if count >= 2:
            dominant_offset[direction] = offset

    def is_dominant_band(row: LinkRow) -> bool:
        offset = row_number_offset(row)
        return offset is not None and dominant_offset.get(row.direction) == offset

    def names_receiver(row: LinkRow) -> bool:
        oriented = row_source_receiver(row)
        if oriented is None:
            return False
        receiver_project = oriented[2]
        return receiver_project.lower() in row.evidence.lower()

    def better(candidate: LinkRow, current: LinkRow) -> bool:
        c_rank = CONFIDENCE_RANK.get(candidate.confidence, 9)
        cur_rank = CONFIDENCE_RANK.get(current.confidence, 9)
        if c_rank != cur_rank:
            return c_rank < cur_rank
        if is_dominant_band(candidate) != is_dominant_band(current):
            return is_dominant_band(candidate)
        if names_receiver(candidate) != names_receiver(current):
            return names_receiver(candidate)
        return False

    best: dict[tuple[str, str, str, str], LinkRow] = {}
    passthrough: list[LinkRow] = []
    for row in rows:
        if row.link_type != "comment-role":
            passthrough.append(row)
            continue
        oriented = row_source_receiver(row)
        if oriented is None:
            passthrough.append(row)
            continue
        _source_project, _source_device, receiver_project, receiver_device = oriented
        key = (row.role, row.direction, receiver_project, receiver_device)
        current = best.get(key)
        if current is None or better(row, current):
            best[key] = row
    return [*passthrough, *best.values()]


def exact_device_links(a_devices: dict[str, DeviceInfo], b_devices: dict[str, DeviceInfo]) -> list[LinkRow]:
    rows: dict[tuple[str, str, str, str, str], LinkRow] = {}
    for device, a in a_devices.items():
        b = b_devices.get(device)
        if not b or a.device_type not in NETWORK_TYPES:
            continue
        direction = complementary_direction(a, b)
        if not direction:
            continue
        add_link(
            rows,
            LinkRow(
                project_a=a.project,
                device_a=a.device,
                project_b=b.project,
                device_b=b.device,
                link_type="exact-device",
                link_addr=device,
                direction=direction,
                confidence="high",
                role=a.role or b.role,
                evidence="shared link device with complementary read/write usage",
            ),
        )
    return list(rows.values())


@dataclass(frozen=True)
class Adjacency:
    upstream: str
    downstream: str


def has_arrow(comment: str) -> bool:
    return "⇒" in comment or "→" in comment or "←" in comment


def names_project(comment: str, project: str) -> bool:
    return project.lower() in comment.lower()


def names_other_equipment(info: DeviceInfo, peer_project: str) -> bool:
    allowed = {info.project.upper(), peer_project.upper()}
    text = " ".join([info.comment, *(info.titles or set())])
    for match in EQUIPMENT_NAME_RE.finditer(text):
        name = match.group(0).upper()
        if name not in allowed:
            return True
    return False


def explicit_peer_marker(info: DeviceInfo, other_project: str) -> bool:
    return names_project(info.comment, other_project) and has_arrow(info.comment)


def sends_downstream(comment: str) -> bool:
    return "下流側へ" in comment or "下流へ" in comment


def sends_upstream(comment: str) -> bool:
    return "上流側へ" in comment or "上流へ" in comment


def reads_from_previous(comment: str) -> bool:
    return "前工程" in comment and "より" in comment


def reads_from_next(comment: str) -> bool:
    return "後工程" in comment and "より" in comment


def source_dest(a: DeviceInfo, b: DeviceInfo) -> tuple[DeviceInfo, DeviceInfo] | None:
    if a.has_write and b.has_read:
        return a, b
    if b.has_write and a.has_read:
        return b, a
    return None


def directionally_valid(a: DeviceInfo, b: DeviceInfo, adjacencies: set[tuple[str, str]]) -> tuple[bool, bool]:
    """Return (valid, receiver_conflict).

    receiver_conflict is True when the pair only passed via explicit_peer_marker
    but the receiver comment names a previous/next-process source that
    contradicts the declared adjacency direction.
    """
    sd = source_dest(a, b)
    if sd is None:
        return False, False
    source, dest = sd
    if explicit_peer_marker(source, dest.project) or explicit_peer_marker(dest, source.project):
        conflict = False
        if (source.project, dest.project) in adjacencies and reads_from_next(dest.comment):
            # dest is downstream of source, yet its comment says the value
            # comes from the next process.
            conflict = True
        elif (dest.project, source.project) in adjacencies and reads_from_previous(dest.comment):
            # dest is upstream of source, yet its comment says the value
            # comes from the previous process.
            conflict = True
        return True, conflict
    if (source.project, dest.project) in adjacencies:
        return sends_downstream(source.comment) and reads_from_previous(dest.comment), False
    if (dest.project, source.project) in adjacencies:
        return sends_upstream(source.comment) and reads_from_next(dest.comment), False
    return False, False


def comment_role_links(
    a_devices: dict[str, DeviceInfo],
    b_devices: dict[str, DeviceInfo],
    adjacencies: set[tuple[str, str]],
) -> list[LinkRow]:
    by_role_b: dict[str, list[DeviceInfo]] = defaultdict(list)
    for b in b_devices.values():
        if b.role and b.communication_like:
            by_role_b[b.role].append(b)

    rows: dict[tuple[str, str, str, str, str], LinkRow] = {}
    for a in a_devices.values():
        if not a.role or not a.communication_like:
            continue
        for b in by_role_b.get(a.role, []):
            direction = complementary_direction(a, b)
            if not direction:
                continue
            if a.device_type not in NETWORK_TYPES and b.device_type not in NETWORK_TYPES:
                continue
            if names_other_equipment(a, b.project) or names_other_equipment(b, a.project):
                continue
            valid, receiver_conflict = directionally_valid(a, b, adjacencies)
            if not valid:
                continue
            confidence = "high" if (a.device_type in NETWORK_TYPES or b.device_type in NETWORK_TYPES) else "medium"
            evidence = f"comment role match: {a.comment!r} <-> {b.comment!r}"
            if receiver_conflict:
                confidence = "medium"
                evidence += " [receiver comment direction conflicts with adjacency]"
            add_link(
                rows,
                LinkRow(
                    project_a=a.project,
                    device_a=a.device,
                    project_b=b.project,
                    device_b=b.device,
                    link_type="comment-role",
                    link_addr=f"role:{a.role}",
                    direction=direction,
                    confidence=confidence,
                    role=a.role,
                    evidence=evidence,
                ),
            )
    return list(rows.values())


def build_links(
    projects: list[ProjectSpec],
    adjacencies: set[tuple[str, str]],
) -> tuple[list[LinkRow], dict[str, dict[str, DeviceInfo]]]:
    loaded = {spec.label: load_project_devices(spec) for spec in projects}
    rows: list[LinkRow] = []
    for i, a in enumerate(projects):
        for b in projects[i + 1 :]:
            a_devices = loaded[a.label]
            b_devices = loaded[b.label]
            rows.extend(exact_device_links(a_devices, b_devices))
            rows.extend(comment_role_links(a_devices, b_devices, adjacencies))
    dedup: dict[tuple[str, str, str, str, str], LinkRow] = {}
    for row in rows:
        add_link(dedup, row)
    rows = dedupe_comment_role_links(list(dedup.values()))
    return sorted(rows, key=lambda r: (r.project_a, r.device_a, r.project_b, r.device_b, r.link_type)), loaded


def write_db(path: Path, projects: list[ProjectSpec], rows: list[LinkRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        drop table if exists link_map;
        drop table if exists project;
        create table project(
            label text primary key,
            root text not null,
            xref_db text not null
        );
        create table link_map(
            id integer primary key autoincrement,
            project_a text not null,
            device_a text not null,
            project_b text not null,
            device_b text not null,
            link_type text not null,
            link_addr text,
            direction text,
            confidence text,
            role text,
            evidence text
        );
        create index idx_link_a on link_map(project_a, device_a);
        create index idx_link_b on link_map(project_b, device_b);
        """
    )
    con.executemany(
        "insert into project(label, root, xref_db) values (?, ?, ?)",
        [(p.label, str(p.root), str(p.db)) for p in projects],
    )
    con.executemany(
        """
        insert into link_map(
            project_a, device_a, project_b, device_b, link_type, link_addr,
            direction, confidence, role, evidence
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                r.project_a,
                r.device_a,
                r.project_b,
                r.device_b,
                r.link_type,
                r.link_addr,
                r.direction,
                r.confidence,
                r.role,
                r.evidence,
            )
            for r in rows
        ],
    )
    con.commit()
    con.close()


def parse_adjacency(text: str) -> tuple[str, str]:
    parts = text.split(":")
    if len(parts) != 3 or parts[1].lower() not in {"downstream", "down", "下流"}:
        raise argparse.ArgumentTypeError("adjacency must be UPSTREAM:downstream:DOWNSTREAM")
    return parts[0], parts[2]


def open_link_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"link-map db not found: {path} (run: gx3_cli.py link-map build)")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def query_links(path: Path, project: str, device: str) -> list[sqlite3.Row]:
    con = open_link_db(path)
    try:
        return con.execute(
            """
            select *, project_b as other_project, device_b as other_device
            from link_map
            where project_a=? and device_a=?
            union all
            select *, project_a as other_project, device_a as other_device
            from link_map
            where project_b=? and device_b=?
            order by confidence desc, other_project, other_device
            """,
            (project, device, project, device),
        ).fetchall()
    finally:
        con.close()


def command_build(args: argparse.Namespace) -> int:
    projects = [parse_project_spec(text) for text in args.project]
    if len(projects) < 2:
        raise SystemExit("build requires at least two --project LABEL=ROOT entries")
    adjacencies = set(args.adjacent or [])
    rows, _loaded = build_links(projects, adjacencies)
    if args.require_pair:
        required = [tuple(item.split(":", 3)) for item in args.require_pair]
        present = {
            (r.project_a, r.device_a, r.project_b, r.device_b)
            for r in rows
        } | {
            (r.project_b, r.device_b, r.project_a, r.device_a)
            for r in rows
        }
        missing = [item for item in required if item not in present]
        if missing:
            raise SystemExit(f"missing required link pairs: {missing}")
    out = Path(args.out)
    write_db(out, projects, rows)
    print(f"link-map written: {out}")
    print(f"projects={len(projects)} links={len(rows)}")
    for row in rows[: args.preview]:
        print(
            f"  {row.project_a}:{row.device_a} <-> {row.project_b}:{row.device_b} "
            f"{row.link_type} {row.direction} {row.confidence} {row.role}"
        )
    if len(rows) > args.preview:
        print(f"  ... {len(rows) - args.preview} more")
    return 0


def command_query(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    rows = query_links(Path(args.db), args.project, device)
    if args.format == "csv":
        fields = [
            "project_a",
            "device_a",
            "project_b",
            "device_b",
            "other_project",
            "other_device",
            "link_type",
            "link_addr",
            "direction",
            "confidence",
            "role",
            "evidence",
        ]
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})
        return 0
    print(f"links for {args.project}:{device}")
    if not rows:
        print("(none)")
        return 1
    for row in rows[: args.limit]:
        print(
            f"- {row['other_project']}:{row['other_device']} "
            f"type={row['link_type']} dir={row['direction']} confidence={row['confidence']} role={row['role']}"
        )
        if args.evidence:
            print(f"  evidence: {row['evidence']}")
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} more")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="build link_map.sqlite from xref DBs")
    p.add_argument("--project", action="append", required=True, help="LABEL=extracted_root; repeat for each PLC")
    p.add_argument(
        "--adjacent",
        action="append",
        type=parse_adjacency,
        default=[],
        help="physical handoff order UPSTREAM:downstream:DOWNSTREAM; may repeat",
    )
    p.add_argument("--out", default=".gx3_index/link_map.sqlite")
    p.add_argument("--preview", type=int, default=30)
    p.add_argument(
        "--require-pair",
        action="append",
        default=[],
        help="test fixture pair PROJECT:DEVICE:PROJECT:DEVICE; may repeat",
    )
    p.set_defaults(func=command_build)

    p = sub.add_parser("query", help="query links for one project/device")
    p.add_argument("device")
    p.add_argument("--project", required=True)
    p.add_argument("--db", default=".gx3_index/link_map.sqlite")
    p.add_argument("--format", choices=["text", "csv"], default="text")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--evidence", action="store_true")
    p.set_defaults(func=command_query)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
