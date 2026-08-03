from __future__ import annotations

"""Local pseudonymization for support bundles and AI-bound text."""

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_project_paths import find_comment_db


ALIAS_DIR = Path(".gx3_redaction")
IP_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f][\u3040-\u30ff\u3400-\u9fff\uff66-\uff9fA-Za-z0-9_ -]*")
PROJECT_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9_-]{2,}(?:\.[A-Za-z0-9]+)?\b")
ALIAS_TOKEN_RE = re.compile(r"^(?:PROJECT|PATH|TEXT|POU|IP)_\d{4}$")
SAFE_PROJECT_WORDS = {
    "AI",
    "ASCII",
    "BYO",
    "CSV",
    "DB",
    "GX3",
    "GX",
    "JSON",
    "MCP",
    "PLC",
    "POU",
    "README",
    "SQLITE",
    "UTF",
}
SENSITIVE_COLUMNS = {
    "comment",
    "japanese",
    "english",
    "all_text",
    "cmtdata",
    "title",
    "pou",
    "label",
    "root",
    "project",
    "project_a",
    "project_b",
    "sender_project",
    "receiver_project",
}


@dataclass
class RedactionMap:
    path: Path
    real_to_alias: dict[str, str] = field(default_factory=dict)
    alias_to_real: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "RedactionMap":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                path=path,
                real_to_alias=dict(data.get("real_to_alias", {})),
                alias_to_real=dict(data.get("alias_to_real", {})),
                counts=dict(data.get("counts", {})),
            )
        return cls(path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "real_to_alias": self.real_to_alias,
            "alias_to_real": self.alias_to_real,
            "counts": self.counts,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def alias_for(self, real: str, kind: str) -> str:
        value = normalize_secret(real)
        if not value:
            return ""
        existing = self.real_to_alias.get(value)
        if existing:
            return existing
        index = int(self.counts.get(kind, 0)) + 1
        self.counts[kind] = index
        alias = f"{kind}_{index:04d}"
        while alias in self.alias_to_real:
            index += 1
            self.counts[kind] = index
            alias = f"{kind}_{index:04d}"
        self.real_to_alias[value] = alias
        self.alias_to_real[alias] = value
        return alias


def normalize_secret(value: object) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text)


def looks_identifying(text: str) -> bool:
    if CJK_RE.search(text):
        return True
    return bool(re.search(r"[A-Za-z]", text))


def map_path_for(root: Path, label: str | None = None) -> Path:
    raw = f"{root.resolve()}|{label or root.name or 'project'}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]
    return ALIAS_DIR / f"aliases_{digest}.json"


def add_candidate(candidates: dict[str, str], value: object, kind: str) -> None:
    text = normalize_secret(value)
    if len(text) < 3:
        return
    if text.upper() in SAFE_PROJECT_WORDS:
        return
    if not looks_identifying(text):
        return
    candidates[text] = kind


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_text_values(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    values: list[tuple[str, str]] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = [str(row[0]) for row in con.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            columns = [str(row[1]) for row in con.execute(f"pragma table_info({quote_ident(table)})")]
            wanted = [c for c in columns if c.lower() in SENSITIVE_COLUMNS]
            if not wanted:
                continue
            select_list = ", ".join(quote_ident(c) for c in wanted)
            for row in con.execute(f"select {select_list} from {quote_ident(table)}"):
                for col in wanted:
                    value = normalize_secret(row[col])
                    if value:
                        kind = "PROJECT" if col.lower() in {"label", "root", "project", "project_a", "project_b", "sender_project", "receiver_project"} else "TEXT"
                        if col.lower() == "pou":
                            kind = "POU"
                        values.append((value, kind))
        con.close()
    except sqlite3.Error:
        return values
    return values


def collect_candidates(root: Path, text: str = "") -> dict[str, str]:
    root = root.resolve()
    candidates: dict[str, str] = {}
    for part, kind in [(str(root), "PATH"), (root.name, "PROJECT"), (root.stem, "PROJECT")]:
        add_candidate(candidates, part, kind)
    for token in PROJECT_TOKEN_RE.findall(text):
        if ALIAS_TOKEN_RE.fullmatch(token):
            continue
        if token.upper() not in SAFE_PROJECT_WORDS and not re.fullmatch(r"[A-Z]{1,3}\d+", token):
            add_candidate(candidates, token, "PROJECT")
    for ip in IP_RE.findall(text):
        add_candidate(candidates, ip, "IP")
    for cjk in CJK_RUN_RE.findall(text):
        add_candidate(candidates, cjk, "TEXT")

    comment_db = find_comment_db(root)
    if comment_db:
        for value, kind in sqlite_text_values(comment_db):
            add_candidate(candidates, value, "TEXT" if kind == "TEXT" else kind)

    index_dir = Path(".gx3_index")
    if index_dir.exists():
        label = re.sub(r"[^A-Za-z0-9_.-]+", "_", root.name.removeprefix("_extracted_")).strip("_") or "project"
        db_paths = [
            index_dir / f"{label}.sqlite",
            index_dir / f"{label}_xref.sqlite",
            index_dir / "link_map.sqlite",
        ]
        for db_path in [p for p in db_paths if p.exists()]:
            for value, kind in sqlite_text_values(db_path):
                add_candidate(candidates, value, kind)
    return candidates


def redact_text(text: str, root: Path, table: RedactionMap) -> str:
    candidates = collect_candidates(root, text)
    for real, kind in candidates.items():
        table.alias_for(real, kind)
    known = [real for real in table.real_to_alias if real and looks_identifying(real) and real in text]
    redacted = text
    for real in sorted(known, key=len, reverse=True):
        redacted = redacted.replace(real, table.real_to_alias[real])
    redacted = IP_RE.sub(lambda m: table.alias_for(m.group(0), "IP"), redacted)
    redacted = CJK_RUN_RE.sub(lambda m: table.alias_for(m.group(0), "TEXT"), redacted)
    table.save()
    return redacted


def restore_text(text: str, table: RedactionMap) -> str:
    restored = text
    for alias, real in sorted(table.alias_to_real.items(), key=lambda item: len(item[0]), reverse=True):
        restored = restored.replace(alias, real)
    return restored


def leak_findings(text: str, table: RedactionMap | None = None) -> list[str]:
    findings: list[str] = []
    ips = sorted(set(IP_RE.findall(text)))
    if ips:
        findings.append(f"unredacted IP address count={len(ips)}")
    if CJK_RE.search(text):
        findings.append("unredacted CJK/halfwidth-kana text")
    if table:
        leaks = [real for real in table.real_to_alias if real and looks_identifying(real) and real in text]
        if leaks:
            findings.append(f"known alias-table secret appears in output count={len(leaks)}")
    return findings


def assert_no_leaks(text: str, table: RedactionMap | None = None) -> None:
    findings = leak_findings(text, table)
    if findings:
        raise SystemExit("redaction leak check failed: " + "; ".join(findings))


def redact_file(path: Path, root: Path, table: RedactionMap) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    redacted = redact_text(text, root, table)
    assert_no_leaks(redacted, table)
    path.write_text(redacted, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Redact, restore, or check GX3 AI-bound text.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["redact", "restore", "check"]:
        p = sub.add_parser(name)
        p.add_argument("input", type=Path)
        p.add_argument("-o", "--output", type=Path)
        p.add_argument("--root", type=Path, default=Path("."))
        p.add_argument("--map", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    table = RedactionMap.load(args.map or map_path_for(args.root))
    text = args.input.read_text(encoding="utf-8", errors="replace")
    if args.command == "redact":
        out = redact_text(text, args.root, table)
        assert_no_leaks(out, table)
    elif args.command == "restore":
        out = restore_text(text, table)
    else:
        assert_no_leaks(text, table)
        out = text
    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        print(out, end="" if out.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
