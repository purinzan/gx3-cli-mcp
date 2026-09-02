from __future__ import annotations

"""One-command read-only audit bundle for a GX3 project."""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from gx3cli.gx3_cli import BASE_DIR, cli_argv, project_label_from_root, python_env
from gx3cli.gx3_project_paths import (
    LEGACY_OUTPUT_PREFIX_ENV,
    OUTPUT_PREFIX_ENV,
    default_project_root,
)


def run_step(name: str, args: list[str], out_dir: Path, root: Path) -> dict[str, object]:
    log = out_dir / f"{name}.log"
    env = python_env(str(root))
    # Steps run with cwd=BASE_DIR, so a default output path lands inside the
    # installed package. Point it at the bundle instead.
    env[OUTPUT_PREFIX_ENV] = str(out_dir / "project")
    env[LEGACY_OUTPUT_PREFIX_ENV] = env[OUTPUT_PREFIX_ENV]
    completed = subprocess.run(
        cli_argv(args),
        cwd=BASE_DIR,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log.write_text(completed.stdout, encoding="utf-8")
    return {"name": name, "returncode": completed.returncode, "log": str(log), "command": ["gx3-cli", *args]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a compact audit bundle: doctor, index, xref, lint, dead-logic.")
    parser.add_argument("--root", default=str(default_project_root(BASE_DIR)))
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--skip-build", action="store_true", help="do not rebuild index/xref")
    parser.add_argument("--skip-lint", action="store_true")
    parser.add_argument("--skip-dead-logic", action="store_true")
    parser.add_argument("--skip-network-map", action="store_true")
    parser.add_argument("--warn-only", action="store_true", help="return 0 even if a step fails")
    args = parser.parse_args(argv)

    root = Path(args.root)
    label = project_label_from_root(root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("outputs") / f"{label}_audit_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_db = Path(".gx3_index") / f"{label}.sqlite"
    xref_db = Path(".gx3_index") / f"{label}_xref.sqlite"
    # These paths are created relative to the caller's cwd but handed to steps
    # that run with cwd=BASE_DIR, so a relative one resolved somewhere else and
    # lint died writing its CSVs into a directory that did not exist there.
    out_dir = out_dir.resolve()
    index_db = index_db.resolve()
    xref_db = xref_db.resolve()

    steps: list[tuple[str, list[str]]] = [
        ("doctor_before", ["doctor", "--root", str(root), "--warn-only"]),
    ]
    if not args.skip_build:
        steps.extend(
            [
                ("index_lite_build", ["index-lite", "build", "--root", str(root), "--out", str(index_db)]),
                ("xref_build", ["xref", "build", "--root", str(root), "--db", str(xref_db)]),
            ]
        )
    if not args.skip_lint:
        steps.append(
            (
                "lint",
                [
                    "lint",
                    str(root),
                    "--xref-db",
                    str(xref_db),
                    "--index-db",
                    str(index_db),
                    "--out-prefix",
                    str(out_dir / "lint"),
                ],
            )
        )
    # Every step writes into the bundle. Left to themselves they write to
    # "outputs" relative to their cwd, which is BASE_DIR -- so an audit filled
    # gx3cli/outputs inside the installed package with one project data, and
    # left it there.
    if not args.skip_dead_logic:
        steps.append(
            (
                "dead_logic",
                ["dead-logic", "--root", str(root), "--db", str(xref_db), "--output-dir", str(out_dir)],
            )
        )
    if not args.skip_network_map:
        steps.append(
            (
                "network_map",
                ["network-map", "--root", str(root), "--index-db", str(index_db), "--output-dir", str(out_dir)],
            )
        )
    steps.append(("doctor_after", ["doctor", "--root", str(root), "--warn-only"]))

    results = [run_step(name, command, out_dir, root) for name, command in steps]
    summary = {
        "root": str(root),
        "label": label,
        "created_at": stamp,
        "steps": results,
    }
    summary_path = out_dir / "audit_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"audit bundle: {out_dir}")
    for result in results:
        print(f"{result['returncode']:<3} {result['name']:<18} {result['log']}")
    if any(int(r["returncode"]) != 0 for r in results) and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
