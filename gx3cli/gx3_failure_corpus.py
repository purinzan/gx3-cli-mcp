from __future__ import annotations

"""Capture failed GX3 parses as a reusable regression corpus."""

import argparse
import json
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from gx3cli.gx3_cli import BASE_DIR, cli_argv, python_env
from gx3cli.gx3_format import GX3FormatInventory, build_format_inventory
from gx3cli.gx3_project_paths import resolve_project_root


DEFAULT_CORPUS = ".gx3_failures"
MANIFEST = "manifest.json"
ALLOWED_REPLAY_MODULES = {
    "gx3cli.gx3_cli",
    "gx3cli.gx3_doctor",
    "gx3cli.gx3_xref",
    "gx3cli.gx3_ladder_print",
    "gx3cli.gx3_index_lite",
    "gx3cli.gx3_lint",
    "gx3cli.analyze_gx3_intermediate_parse_gaps",
    "gx3cli.review_gx3_project",
}
SHELL_UNSAFE_RE = re.compile(r"[\n\r;&|`<>]|\$\(")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-._")
    return value or "gx3-failure"


def read_manifest(corpus: Path) -> dict[str, object]:
    path = corpus / MANIFEST
    if not path.exists():
        return {"version": 1, "cases": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(corpus: Path, manifest: dict[str, object]) -> None:
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return str(path)


def copy_project_root(src: Path, dst: Path) -> None:
    def ignore(_dir: str, names: list[str]) -> set[str]:
        return {name for name in names if name in {"__pycache__", ".pytest_cache", ".gx3_index"}}

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def extract_gx3(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    dst_resolved = dst.resolve()
    with zipfile.ZipFile(src) as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit(f"cannot capture corrupt gx3 archive; first bad member: {bad}")
        for member in zf.infolist():
            name = member.filename
            if not name or name.endswith("/"):
                continue
            target = (dst / name).resolve()
            if target != dst_resolved and dst_resolved not in target.parents:
                raise SystemExit(f"unsafe gx3 archive member path: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src_file, target.open("wb") as dst_file:
                shutil.copyfileobj(src_file, dst_file)


def normalize_case_id(case_id: str | None, source: Path) -> str:
    if case_id:
        return slugify(case_id)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(source.stem or source.name)}"


def capture_case(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    source = Path(args.root or args.gx3)
    case_id = normalize_case_id(args.case_id, source)
    case_dir = corpus / "cases" / case_id
    if case_dir.exists() and not args.overwrite:
        print(f"case already exists: {case_id} (use --overwrite)", file=sys.stderr)
        return 2
    case_dir.mkdir(parents=True, exist_ok=True)

    project_dir = case_dir / "project"
    archive_path = ""
    source_kind = "root"
    if args.gx3:
        gx3 = Path(args.gx3)
        if not gx3.exists():
            print(f"missing gx3: {gx3}", file=sys.stderr)
            return 2
        source_kind = "gx3"
        archive = case_dir / gx3.name
        shutil.copy2(gx3, archive)
        archive_path = display_path(archive, case_dir)
        extract_gx3(gx3, project_dir)
    else:
        root = resolve_project_root(str(args.root))
        copy_project_root(root, project_dir)

    case_meta = {
        "id": case_id,
        "created_at": now_iso(),
        "source": str(source),
        "source_kind": source_kind,
        "project": display_path(project_dir, case_dir),
        "archive": archive_path,
        "reason": args.reason,
        "failed_command": args.failed_command,
        "expected": {
            "doctor": args.expect_doctor,
            "xref": args.expect_xref,
            "ladder_print": args.expect_ladder_print,
            "failed_command": bool(args.failed_command) and args.expect_failed_command,
        },
        "notes": args.notes,
        "active": not args.inactive,
    }
    (case_dir / "case.json").write_text(json.dumps(case_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = read_manifest(corpus)
    cases = [c for c in manifest.get("cases", []) if isinstance(c, dict) and c.get("id") != case_id]
    cases.append({"id": case_id, "case_file": display_path(case_dir / "case.json", corpus), "active": not args.inactive})
    manifest["cases"] = sorted(cases, key=lambda c: str(c.get("id", "")))
    write_manifest(corpus, manifest)
    print(f"captured failure case: {case_id}")
    print(f"case file: {case_dir / 'case.json'}")
    return 0


def lddb_files(root: Path) -> list[Path]:
    return sorted(root.glob("*_LDDB.db"))


def check_schema(root: Path, inventory: GX3FormatInventory) -> tuple[bool, str]:
    lddbs = lddb_files(root)
    if not lddbs:
        detail = inventory.unsupported_program_detail()
        if detail.startswith("unsupported/"):
            return True, f"no *_LDDB.db files; {detail}"
        return False, "no *_LDDB.db files and no known GX3 program DB files"
    details: list[str] = []
    required = {"id", "pos", "blocktype", "data", "rowsize", "translated", "ConvTarget"}
    for db in lddbs:
        try:
            con = sqlite3.connect(db)
            columns = {row[1] for row in con.execute("pragma table_info(LadderBlocks)").fetchall()}
            count = con.execute("select count(*) from LadderBlocks").fetchone()[0]
            con.close()
        except sqlite3.Error as exc:
            return False, f"{db.name}: {exc}"
        missing = sorted(required - columns)
        if missing:
            return False, f"{db.name}: LadderBlocks missing {', '.join(missing)}"
        details.append(f"{db.name}: rows={count}")
    return True, "; ".join(details)


def run_command(name: str, command: list[str], root: Path, log_dir: Path) -> tuple[int, str]:
    log = log_dir / f"{name}.log"
    completed = subprocess.run(
        cli_argv(command),
        cwd=BASE_DIR,
        env=python_env(str(root)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log.write_text(completed.stdout, encoding="utf-8")
    return completed.returncode, str(log)


def replay_argv(command: str, root: Path, log_dir: Path) -> list[str]:
    if SHELL_UNSAFE_RE.search(command):
        raise ValueError("failed_command contains shell metacharacters; store a gx3-cli or python -m gx3cli command instead")
    tokens = [
        token.replace("{root}", str(root)).replace("{reports_dir}", str(log_dir))
        for token in shlex.split(command)
    ]
    if not tokens:
        raise ValueError("failed_command is empty")

    executable = Path(tokens[0]).name
    if executable == "gx3-cli":
        return cli_argv(tokens[1:])

    if len(tokens) >= 3 and tokens[1] == "-m":
        module = tokens[2]
        if module == "gx3cli.gx3_cli":
            return cli_argv(tokens[3:])
        if module in ALLOWED_REPLAY_MODULES:
            return [sys.executable, "-m", module, *tokens[3:]]

    raise ValueError("failed_command replay only supports gx3-cli or allowed python -m gx3cli.* commands")


def run_replay_command(command: str, root: Path, log_dir: Path) -> tuple[int, str]:
    log = log_dir / "failed_command.log"
    try:
        argv = replay_argv(command, root, log_dir)
    except ValueError as exc:
        log.write_text(f"{exc}\ncommand: {command}\n", encoding="utf-8")
        return 2, str(log)

    completed = subprocess.run(
        argv,
        cwd=BASE_DIR,
        env=python_env(str(root)),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log.write_text(completed.stdout, encoding="utf-8")
    return completed.returncode, str(log)


def run_case(case_file: Path, reports_dir: Path) -> dict[str, object]:
    meta = json.loads(case_file.read_text(encoding="utf-8"))
    case_id = str(meta["id"])
    root = Path(str(meta["project"]))
    if not root.is_absolute():
        root = case_file.parent / root
    root = root.resolve()
    if not root.exists() and meta.get("archive"):
        root = reports_dir / case_id / "project"
        archive = Path(str(meta["archive"]))
        if not archive.is_absolute():
            archive = case_file.parent / archive
        extract_gx3(archive, root)
        root = root.resolve()
    log_dir = reports_dir / case_id
    log_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, object]] = []
    inventory = build_format_inventory(root)
    checks.append({"name": "format_inventory", "passed": True, "detail": inventory.detail(), "formats": inventory.as_dict()})

    has_lddb = inventory.has_ladder
    unsupported_detail = inventory.unsupported_program_detail()
    ok, detail = check_schema(root, inventory)
    checks.append({"name": "schema", "passed": ok, "detail": detail})

    expectations = meta.get("expected", {}) if isinstance(meta.get("expected"), dict) else {}
    if expectations.get("doctor", True):
        code, log = run_command("doctor", ["doctor", "--root", str(root), "--warn-only", "--no-script-check"], root, log_dir)
        checks.append({"name": "doctor", "passed": code == 0, "returncode": code, "log": log})
    if expectations.get("xref", True):
        if has_lddb:
            db = log_dir / "xref.sqlite"
            code, log = run_command("xref", ["xref", "build", "--root", str(root), "--db", str(db)], root, log_dir)
            checks.append({"name": "xref", "passed": code == 0, "returncode": code, "log": log})
        else:
            checks.append({"name": "xref", "passed": True, "detail": f"skipped: no LDDB; {unsupported_detail}"})
    if expectations.get("ladder_print", True):
        if has_lddb:
            passed = True
            parts: list[str] = []
            for lddb in lddb_files(root):
                code, log = run_command(
                    f"ladder_print_{lddb.stem}",
                    ["ladder-print", lddb.name, "--root", str(root), "--list-sections"],
                    root,
                    log_dir,
                )
                passed = passed and code == 0
                parts.append(f"{lddb.name}: rc={code} log={log}")
            checks.append({"name": "ladder_print", "passed": passed, "detail": "; ".join(parts)})
        else:
            checks.append({"name": "ladder_print", "passed": True, "detail": f"skipped: no LDDB; {unsupported_detail}"})

    failed_command = str(meta.get("failed_command") or "").strip()
    if failed_command and expectations.get("failed_command", True):
        code, log = run_replay_command(failed_command, root, log_dir)
        checks.append({"name": "failed_command", "passed": code == 0, "returncode": code, "log": log})

    failed = [check for check in checks if not check["passed"]]
    result = {"id": case_id, "passed": not failed, "checks": checks}
    (log_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def iter_active_case_files(corpus: Path) -> list[Path]:
    manifest = read_manifest(corpus)
    out: list[Path] = []
    for item in manifest.get("cases", []):
        if not isinstance(item, dict) or not item.get("active", True):
            continue
        case_file = Path(str(item.get("case_file", "")))
        if not case_file.is_absolute():
            case_file = corpus / case_file
        out.append(case_file)
    return out


def run_corpus(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    case_files = iter_active_case_files(corpus)
    if not case_files:
        print(f"no active failure cases in {corpus}")
        return 0
    reports_dir = Path(args.reports_dir) if args.reports_dir else corpus / "reports" / datetime.now().strftime("%Y%m%d-%H%M%S")
    reports_dir = reports_dir.resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)

    results = [run_case(case_file, reports_dir) for case_file in case_files]
    summary = {"created_at": now_iso(), "corpus": str(corpus), "results": results}
    (reports_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"{status} {result['id']}")
        for check in result["checks"]:
            mark = "ok" if check["passed"] else "ng"
            detail = check.get("detail") or check.get("log", "")
            print(f"  {mark:<2} {check['name']}: {detail}")
    print(f"reports: {reports_dir}")
    return 1 if any(not result["passed"] for result in results) and not args.warn_only else 0


def init_corpus(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus)
    write_manifest(corpus, read_manifest(corpus))
    readme = corpus / "README.md"
    if not readme.exists():
        readme.write_text(
            "\n".join(
                [
                    "# GX3 failure corpus",
                    "",
                    "Captured projects in this folder are regression fixtures.",
                    "Add a case whenever GX3 parsing, printing, indexing, or linting fails in a way that should not regress.",
                    "Use `{root}` and `{reports_dir}` placeholders in `--failed-command` so replay stays portable.",
                    "",
                    "Commands:",
                    "",
                    "```sh",
                    "gx3-cli failure-corpus capture --root path/to/extracted --case-id short-name --reason \"what failed\"",
                    "gx3-cli failure-corpus run",
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    print(f"initialized failure corpus: {corpus}")
    return 0


def self_test(args: argparse.Namespace) -> int:
    from gx3cli.gx3_synthetic_project import create_synthetic_project

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        root = create_synthetic_project(tmp_path / "fixture")
        corpus = tmp_path / "corpus"
        ns = argparse.Namespace(
            corpus=str(corpus),
            root=str(root),
            gx3=None,
            case_id="synthetic-ok",
            overwrite=False,
            reason="self-test capture",
            failed_command="",
            notes="",
            inactive=False,
            expect_doctor=True,
            expect_xref=True,
            expect_ladder_print=True,
            expect_failed_command=True,
        )
        code = capture_case(ns)
        if code:
            return code
        run_ns = argparse.Namespace(corpus=str(corpus), reports_dir="", warn_only=False)
        return run_corpus(run_ns)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture failed GX3 projects and rerun them as regression fixtures.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create an empty failure corpus")
    p.add_argument("--corpus", default=DEFAULT_CORPUS)
    p.set_defaults(func=init_corpus)

    p = sub.add_parser("capture", help="copy a failed project into the failure corpus")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--root", help="extracted GX3 project root")
    src.add_argument("--gx3", help="GX3 archive to copy and extract")
    p.add_argument("--corpus", default=DEFAULT_CORPUS)
    p.add_argument("--case-id", default="")
    p.add_argument("--reason", required=True, help="what failed and why this should be kept")
    p.add_argument("--failed-command", default="", help="command/output that exposed the failure")
    p.add_argument("--notes", default="")
    p.add_argument("--inactive", action="store_true", help="record the case but skip it during run")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-doctor", dest="expect_doctor", action="store_false")
    p.add_argument("--no-xref", dest="expect_xref", action="store_false")
    p.add_argument("--no-ladder-print", dest="expect_ladder_print", action="store_false")
    p.add_argument(
        "--no-replay-failed-command",
        dest="expect_failed_command",
        action="store_false",
        help="store failed_command as context but do not replay it during corpus runs",
    )
    p.set_defaults(
        func=capture_case,
        expect_doctor=True,
        expect_xref=True,
        expect_ladder_print=True,
        expect_failed_command=True,
    )

    p = sub.add_parser("run", help="run validation checks for all active corpus cases")
    p.add_argument("--corpus", default=DEFAULT_CORPUS)
    p.add_argument("--reports-dir", default="")
    p.add_argument("--warn-only", action="store_true")
    p.set_defaults(func=run_corpus)

    p = sub.add_parser("self-test", help="exercise capture and run using a synthetic project")
    p.set_defaults(func=self_test)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
