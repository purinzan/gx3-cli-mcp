from __future__ import annotations

"""Create a redacted support bundle without ladder body data."""

import argparse
import io
import json
import platform
import subprocess
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from gx3cli.analyze_gx3_intermediate_parse_gaps import ProjectInput, collect_project, project_label_from_root
from gx3cli.gx3_cli import cli_argv, python_env
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.gx3_redaction import RedactionMap, assert_no_leaks, map_path_for, redact_text
from gx3cli.gx3_version import package_version


def redactor(root: Path) -> tuple[RedactionMap, Any]:
    table = RedactionMap.load(map_path_for(root))

    def apply(text: str) -> str:
        out = redact_text(text, root, table)
        assert_no_leaks(out, table)
        return out

    return table, apply


def run_cli_text(args: list[str], root: Path) -> str:
    completed = subprocess.run(
        cli_argv(args),
        env=python_env(str(root)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return "\n".join(
        part
        for part in [
            f"command: gx3-cli {' '.join(args)}",
            f"exit_code: {completed.returncode}",
            completed.stdout.strip(),
            completed.stderr.strip(),
        ]
        if part
    )


def project_inventory(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        rows.append({"path": rel, "suffix": path.suffix.lower(), "size": path.stat().st_size})
    return rows


def parse_gap_summary(root: Path) -> dict[str, object]:
    label = project_label_from_root(root)
    rows = collect_project(ProjectInput(label, root))
    reasons = Counter(str(row["likely_reason"]) for row in rows)
    priorities = Counter(str(row["priority"]) for row in rows)
    trace_impact = sum(1 for row in rows if row["trace_impact"] == "yes")
    return {
        "project": label,
        "gap_rows": len(rows),
        "trace_impact_rows": trace_impact,
        "reasons": dict(sorted(reasons.items())),
        "priorities": dict(sorted(priorities.items())),
    }


def add_text(zf: zipfile.ZipFile, name: str, text: str, redact: Any) -> None:
    payload = redact(text)
    zf.writestr(name, payload.encode("utf-8"))


def add_json(zf: zipfile.ZipFile, name: str, data: object, redact: Any) -> None:
    add_text(zf, name, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", redact)


def build_bundle(root: Path, out: Path) -> Path:
    root = resolve_project_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    _table, redact = redactor(root)
    manifest = {
        "bundle_schema": 1,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "package_version": package_version(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "contains_ladder_body": False,
        "contains_alias_table": False,
    }
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        add_json(zf, "manifest.json", manifest, redact)
        add_text(zf, "doctor.txt", run_cli_text(["doctor", "--root", str(root), "--warn-only"], root), redact)
        add_json(zf, "project_inventory_redacted.json", project_inventory(root), redact)
        add_json(zf, "parse_gap_summary_redacted.json", parse_gap_summary(root), redact)
        add_text(
            zf,
            "README.txt",
            "\n".join(
                [
                    "GX3 redacted support bundle",
                    "This archive intentionally excludes LadderBlocks body data and the local alias table.",
                    "Use it for parser diagnostics only; do not treat it as a safety certification.",
                    "",
                ]
            ),
            redact,
        )
    return out


def default_output() -> str:
    return f"gx3_support_bundle_{time.strftime('%Y%m%d_%H%M%S')}.zip"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a redacted support ZIP without ladder body data.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder or .gx3")
    parser.add_argument("-o", "--output", default=default_output())
    args = parser.parse_args(argv)
    out = build_bundle(resolve_project_root(args.root), Path(args.output))
    print(f"support bundle: {out}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
