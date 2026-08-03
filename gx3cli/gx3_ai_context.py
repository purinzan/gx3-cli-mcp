from __future__ import annotations

"""Create a compact evidence bundle for a device/question."""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from gx3cli.gx3_cli import BASE_DIR, cli_argv, project_label_from_root, python_env
from gx3cli.gx3_project_paths import default_project_root


def run_capture(name: str, command: list[str], out_dir: Path, root: Path) -> Path:
    path = out_dir / f"{name}.txt"
    completed = subprocess.run(
        cli_argv(command),
        cwd=BASE_DIR,
        env=python_env(str(root)),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    path.write_text("$ python gx3_cli.py " + " ".join(command) + "\n\n" + completed.stdout, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bundle compact CLI evidence for GPT review or handoff.")
    parser.add_argument("device", nargs="?", help="target device")
    parser.add_argument("--root", default=str(default_project_root(BASE_DIR)))
    parser.add_argument("--question", default="", help="question or purpose to include in the index")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--max-depth", default="4")
    parser.add_argument("--xref-db", default="")
    parser.add_argument("--link-db", default=".gx3_index/link_map.sqlite")
    args = parser.parse_args(argv)

    root = Path(args.root)
    label = project_label_from_root(root)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_device = (args.device or "project").replace("\\", "_").replace("/", "_")
    out_dir = Path(args.out_dir) if args.out_dir else Path("outputs") / "evidence" / f"{label}_{safe_device}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    files.append(run_capture("context", ["context"], out_dir, root))
    files.append(run_capture("doctor", ["doctor", "--root", str(root), "--warn-only"], out_dir, root))
    if args.device:
        files.append(run_capture("query_device", ["query-device", args.device, "--root", str(root)], out_dir, root))
        files.append(
            run_capture(
                "trace_device",
                ["trace-device", args.device, "--root", str(root), "--strict-logic", "--compact", "--max-depth", str(args.max_depth)],
                out_dir,
                root,
            )
        )
        xref_cmd = ["xref", "where-used", args.device, "--root", str(root)]
        if args.xref_db:
            xref_cmd.extend(["--db", args.xref_db])
        xref_cmd.extend(["--cross", "--link-db", args.link_db])
        files.append(run_capture("where_used", xref_cmd, out_dir, root))

    index = out_dir / "README.md"
    lines = [
        f"# GX3 Evidence Bundle: {label}",
        "",
        f"- Root: `{root}`",
        f"- Device: `{args.device or ''}`",
        f"- Question: {args.question}" if args.question else "- Question: ",
        f"- Created: {stamp}",
        "",
        "## Files",
        "",
    ]
    lines.extend(f"- `{path.name}`" for path in files)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"evidence bundle: {out_dir}")
    print(f"index: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
