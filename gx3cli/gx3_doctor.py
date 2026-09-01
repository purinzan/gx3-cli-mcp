from __future__ import annotations

"""Workspace health check for the GX3 analysis CLI."""

import argparse
import importlib.util
import os
import platform
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from gx3cli.gx3_cli import BASE_DIR, COMMANDS, project_label_from_root
from gx3cli.gx3_project_paths import (
    ProjectRootError,
    archive_container_kind,
    archive_tool_candidates,
    default_project_root,
    resolve_project_root,
)
from gx3cli.gx3_version import package_version


@dataclass
class Check:
    name: str
    status: str
    detail: str


def status_rank(status: str) -> int:
    return {"OK": 0, "WARN": 1, "ERROR": 2}.get(status, 2)


def add(checks: list[Check], name: str, status: str, detail: str) -> None:
    checks.append(Check(name, status, detail))


def next_step(command: str) -> str:
    return f" next: {command}"


def build_command_for(name: str, root: Path, path: Path) -> str:
    if name == "index-lite":
        return f"gx3-cli index-lite build --root {root} --out {path}"
    if name == "xref":
        return f"gx3-cli xref build --root {root} --db {path}"
    return f"rebuild {name} for {root}"


def sqlite_meta(path: Path) -> dict[str, str]:
    con = sqlite3.connect(path)
    try:
        rows = con.execute("select key, value from meta").fetchall()
    finally:
        con.close()
    return {str(k): str(v) for k, v in rows}


def latest_project_mtime(root: Path) -> float:
    latest = 0.0
    for pattern in ("*_LDDB.db", "*_DM.db", "CPU.PRM", "LabelData.db"):
        for path in root.glob(pattern):
            latest = max(latest, path.stat().st_mtime)
    return latest


def check_python_scripts(checks: list[Check]) -> None:
    seen: set[str] = set()
    for command, spec in sorted(COMMANDS.items()):
        if spec.script in seen:
            continue
        seen.add(spec.script)
        if getattr(sys, "frozen", False):
            module_name = spec.script[:-3] if spec.script.endswith(".py") else spec.script
            spec_obj = importlib.util.find_spec(f"gx3cli.{module_name}")
            add(checks, f"script:{command}", "OK" if spec_obj else "ERROR", f"bundled module {module_name}")
            continue
        path = BASE_DIR / spec.script
        if not path.exists():
            add(checks, f"script:{command}", "ERROR", f"missing {spec.script}")
            continue
        spec_obj = importlib.util.spec_from_file_location(f"_doctor_{path.stem}", path)
        if spec_obj is None:
            add(checks, f"script:{command}", "ERROR", f"cannot load spec for {spec.script}")
        else:
            add(checks, f"script:{command}", "OK", spec.script)


def check_runtime(checks: list[Check], index_dir: Path) -> None:
    add(checks, "package-version", "OK", package_version())
    add(checks, "python", "OK", f"{platform.python_version()} ({sys.executable})")
    add(checks, "platform", "OK", platform.platform())
    add(checks, "cwd", "OK", str(Path.cwd()))
    add(checks, "index-dir", "OK" if index_dir.exists() else "WARN", str(index_dir))
    add(checks, "index-db-count", "OK" if index_dir.exists() else "WARN", str(len(list(index_dir.glob('*.sqlite'))) if index_dir.exists() else 0))
    root_env = os.environ.get("PROJECT_ROOT") or os.environ.get("GX3_ROOT") or ""
    add(checks, "root-env", "OK" if root_env else "WARN", root_env or "PROJECT_ROOT/GX3_ROOT not set")
    tools = archive_tool_candidates()
    add(checks, "7z-extractor", "OK" if tools else "WARN", ", ".join(tools) if tools else "not found; set GX3_7Z or install 7-Zip/7zz")


def check_input_path(checks: list[Check], raw_root: Path) -> None:
    path = raw_root.expanduser()
    if not path.exists():
        add(checks, "input-path", "ERROR", f"missing {path}; next: check --root path or set PROJECT_ROOT/GX3_ROOT")
        return
    if path.is_dir():
        add(checks, "input-kind", "OK", "extracted-folder")
        return
    suffix = path.suffix.lower()
    if suffix == ".gx3":
        kind = archive_container_kind(path)
        status = "OK" if kind == "zip" else ("WARN" if kind == "7z" else "ERROR")
        detail = f".gx3 container={kind}"
        if kind == "7z":
            detail += "; may require 7-Zip or GX Works3 export when encrypted"
        add(checks, "input-kind", status, detail)
        return
    add(checks, "input-kind", "WARN", f"file suffix={suffix or '(none)'}; expected .gx3 or extracted folder")


def check_root(checks: list[Check], root: Path) -> None:
    if not root.exists():
        add(checks, "project-root", "ERROR", f"missing {root}; next: check --root path or extract the .gx3 project")
        return
    lddb = list(root.glob("*_LDDB.db"))
    detail = f"{root} ({len(lddb)} LDDB files)"
    if not lddb:
        detail += "; next: run gx3-cli inspect --root <project.gx3> to confirm the input kind"
    add(checks, "project-root", "OK" if lddb else "ERROR", detail)
    for required in ["CPU.PRM", "LabelData.db"]:
        path = root / required
        add(checks, required, "OK" if path.exists() else "WARN", str(path))


def check_db(checks: list[Check], name: str, path: Path, root: Path, stale_is_warn: bool = True) -> None:
    if not path.exists():
        add(checks, name, "WARN", f"missing {path};{next_step(build_command_for(name, root, path))}")
        return
    try:
        meta = sqlite_meta(path)
    except sqlite3.Error as exc:
        add(checks, name, "ERROR", f"cannot read {path}: {exc}")
        return
    db_root = Path(meta.get("root", ""))
    detail = f"{path}"
    if db_root and db_root != root:
        add(checks, name, "WARN", f"{detail}; meta root={db_root};{next_step(build_command_for(name, root, path))}")
        return
    project_mtime = latest_project_mtime(root)
    if project_mtime and path.stat().st_mtime < project_mtime and stale_is_warn:
        add(checks, name, "WARN", f"{detail}; older than project files;{next_step(build_command_for(name, root, path))}")
        return
    add(checks, name, "OK", detail)


def check_link_map(checks: list[Check], link_db: Path) -> None:
    if not link_db.exists():
        add(
            checks,
            "link-map",
            "WARN",
            f"missing {link_db}; next: gx3-cli link-map build --project LABEL=<project-root> --out {link_db}",
        )
        return
    try:
        con = sqlite3.connect(link_db)
        projects = con.execute("select count(*) from project").fetchone()[0]
        links = con.execute("select count(*) from link_map").fetchone()[0]
        con.close()
    except sqlite3.Error as exc:
        add(checks, "link-map", "ERROR", f"cannot read {link_db}: {exc}; next: rebuild with gx3-cli link-map build --project LABEL=<project-root> --out {link_db}")
        return
    detail = f"{link_db}; projects={projects} links={links}"
    if not links:
        detail += "; next: add multiple --project entries or review communication devices"
    add(checks, "link-map", "OK" if links else "WARN", detail)


def print_checks(checks: list[Check]) -> None:
    width = max(len(c.name) for c in checks) if checks else 10
    for check in checks:
        print(f"{check.status:<5} {check.name:<{width}}  {check.detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check GX3 CLI scripts, project root, indexes, and link-map readiness.")
    parser.add_argument("--root", default=str(default_project_root(BASE_DIR)), help="extracted project root")
    parser.add_argument("--index-dir", default=".gx3_index", help="index directory")
    parser.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="cross-project link-map DB")
    parser.add_argument("--warn-only", action="store_true", help="return 0 even when ERROR checks exist")
    parser.add_argument("--no-script-check", action="store_true", help="skip command script presence checks")
    args = parser.parse_args(argv)

    raw_root = Path(args.root)
    label = project_label_from_root(raw_root)
    index_dir = Path(args.index_dir)
    checks: list[Check] = []
    check_runtime(checks, index_dir)
    check_input_path(checks, raw_root)
    try:
        root = resolve_project_root(args.root)
        label = project_label_from_root(root)
    except ProjectRootError as exc:
        add(checks, "project-root", "ERROR", str(exc))
        print_checks(checks)
        return 0 if args.warn_only else 1
    if not args.no_script_check:
        check_python_scripts(checks)
    check_root(checks, root)
    check_db(checks, "index-lite", index_dir / f"{label}.sqlite", root)
    check_db(checks, "xref", index_dir / f"{label}_xref.sqlite", root)
    check_link_map(checks, Path(args.link_db))
    print_checks(checks)
    worst = max((status_rank(c.status) for c in checks), default=0)
    if worst >= 2 and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
