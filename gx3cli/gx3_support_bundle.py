from __future__ import annotations

"""Create a redacted support bundle without ladder body data."""

import argparse
import io
import json
import platform
import re
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


# File names this format fixes. They say nothing about the customer, and
# keeping them is what makes the inventory useful: a missing UnitConfig.dat,
# SourceInfo.CAB or *_LDDB.db is itself diagnostic evidence.
#
# Only whole names, never a suffix with something in front of it. A `.w3pa`
# is a format-defined kind of file and `ProjectFalcon.w3pa` is a customer's
# project name -- the first version of this kept the second because it
# matched on the extension, and the name went straight into the bundle.
FORMAT_NAMES = re.compile(
    r"^(UnitConfig\.dat|LabelData\.db|CPU\.PRM|UNIT\.PRM|SYSTEM\.PRM"
    r"|ConvertData|SourceInfo|SourceInfo\.CAB|_Project\.txc"
    r"|[0-9A-Fa-f]+_(LDDB|DC|MilDB|StepInfo|DM|FBDDB|STDB)\.db"
    r"|[0-9]+\.db)$"
)
STRUCTURAL_ALIAS = re.compile(r"^(?:DIR|FILE)_\d{4}$")


def safe_component(component: str, index: dict[str, str], kind: str) -> str:
    """A path component, or a stand-in for it.

    The redactor works on text it can recognise -- addresses, Japanese, known
    secrets, upper-case tokens. A folder called `CustomerAlpha` or
    `BatteryLine5` is none of those, and the inventory listed every relative
    path in the project, so a bundle meant to be safe to attach to a public
    issue carried the customer's naming.

    Names this format defines are kept, because they are what the inventory is
    for. Everything else becomes a stable stand-in, so two entries under one
    folder still read as being under one folder.
    """
    if FORMAT_NAMES.match(component):
        return component
    if component not in index:
        index[component] = f"{kind}_{len(index) + 1:04d}"
    return index[component]


def project_inventory(root: Path) -> list[dict[str, object]]:
    """What the project holds, without saying what anything is called.

    Suffix, size and depth are kept: they are the diagnostic content. The
    names are not, and no attempt is made here to decide which of them happen
    to be harmless.
    """
    rows: list[dict[str, object]] = []
    folders: dict[str, str] = {}
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        safe = [safe_component(part, folders, "DIR") for part in parts[:-1]]
        safe.append(safe_component(parts[-1], files, "FILE"))
        rows.append(
            {
                "path": "/".join(safe),
                "suffix": path.suffix.lower(),
                "size": path.stat().st_size,
                "depth": len(parts) - 1,
            }
        )
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


def structural_leak_table(table: RedactionMap) -> RedactionMap:
    """Known secrets that are still forbidden in a structural inventory.

    The generic redactor can learn format-defined names from another payload
    such as doctor.txt and put them in the alias table. Those exact names are
    deliberately retained by project_inventory(), so treating them as leaks at
    this boundary makes the two policies contradict each other. Structural
    stand-ins are safe for the same reason.

    Everything else remains in the check: project/customer/equipment names
    already known to the alias table must still fail if they somehow survive
    structural pseudonymization. IP and CJK checks are independent of the table
    and remain active in assert_no_leaks().
    """
    filtered = {
        real: alias
        for real, alias in table.real_to_alias.items()
        if not FORMAT_NAMES.fullmatch(real) and not STRUCTURAL_ALIAS.fullmatch(real)
    }
    return RedactionMap(path=table.path, real_to_alias=filtered)


def add_structural_json(
    zf: zipfile.ZipFile,
    name: str,
    data: object,
    table: RedactionMap,
) -> None:
    """Write data that has already been structurally pseudonymized.

    `project_inventory()` does not contain free-form project names: every
    user-controlled path component has already become DIR_nnnn / FILE_nnnn.
    Running that result through the generic text redactor a second time is not
    safer; it rewrites the stand-ins and format-defined names such as CPU.PRM
    or 001_LDDB.db, destroying the diagnostic information the structural pass
    deliberately retained.

    We still run the leak assertion with only the intentionally-safe exact
    format names removed from the known-secret set. If a CJK/IP/customer name
    reaches this boundary despite the structural rules, fail the bundle instead
    of silently publishing it.
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert_no_leaks(payload, structural_leak_table(table))
    zf.writestr(name, payload.encode("utf-8"))


def build_bundle(root: Path, out: Path) -> Path:
    root = resolve_project_root(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    table, redact = redactor(root)
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
        # The inventory has its own structural anonymization boundary. Do not
        # feed it back through the generic token redactor afterwards.
        add_structural_json(zf, "project_inventory_redacted.json", project_inventory(root), table)
        add_json(zf, "parse_gap_summary_redacted.json", parse_gap_summary(root), redact)
        add_text(
            zf,
            "README.txt",
            "\n".join(
                [
                    "GX3 redacted support bundle",
                    "This archive intentionally excludes LadderBlocks body data and the local alias table.",
                    "Folder and file names are replaced with stand-ins (DIR_0001, FILE_0001);",
                    "names this project format defines are kept, because they are the diagnosis.",
                    "",
                    "What remains, deliberately: file suffixes, sizes and nesting depth. Those",
                    "are the diagnostic content, and a size can identify a file on its own.",
                    "This is a reduction, not a guarantee that the archive holds no secret.",
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
