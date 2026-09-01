from __future__ import annotations

import argparse
import importlib
import inspect
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import sqlite3

from gx3cli.gx3_project_paths import (
    LEGACY_ROOT_ENV,
    REPORT_URL,
    ROOT_ENV,
    ProjectRootError,
    default_comm_prefix,
    default_output_prefix,
    default_project_root,
    project_root_error_message,
    resolve_project_root,
)
from gx3cli.gx3_version import version_line


PACKAGE = "gx3cli"
BASE_DIR = Path(__file__).resolve().parent
PACKAGE_PARENT = BASE_DIR.parent


@dataclass(frozen=True)
class CommandSpec:
    script: str
    summary: str


COMMANDS: dict[str, CommandSpec] = {
    "review": CommandSpec("review_gx3_project.py", "generate static review CSV reports"),
    "trace-device": CommandSpec("trace_gx3_device_dependencies.py", "trace upstream dependencies for one device"),
    "dependency-flow": CommandSpec("gx3_dependency_flow.py", "render upstream coil dependencies as a Mermaid flow graph"),
    "ladder-diagram": CommandSpec("gx3_ladder_diagram.py", "render target device driver rows as ASCII ladder diagrams"),
    "ladder-print": CommandSpec("gx3_ladder_print.py", "render a whole program in GX Works3 print-text layout (matches GX print output)"),
    "matiec-st": CommandSpec("gx3_matiec_export.py", "export target device enable logic as MATIEC Structured Text"),
    "project-survey": CommandSpec("gx3_project_survey.py", "generate ordered project survey package"),
    "external-inputs": CommandSpec("gx3_external_inputs.py", "extract external/terminal contact inputs and trace boundaries"),
    "hmi-build-info": CommandSpec("extract_hmi_build_info.py", "extract I/O, monitor, manual-output, and single-action candidates"),
    "gtx-probe": CommandSpec("gtx_probe.py", "probe GT Designer3 GTX HMI project containers"),
    "comm-refresh": CommandSpec("extract_comm_refresh_areas.py", "extract communication units and refresh areas"),
    "comm-detail": CommandSpec("gx3_comm_detail.py", "extract detailed communication source candidates and AJ65BT-R2N settings"),
    "w3pa-probe": CommandSpec("gx3_w3pa_probe.py", "probe *.w3pa parameter strings, modules, IPs, and device candidates"),
    "link-map": CommandSpec("gx3_link_map.py", "build/query cross-project communication device links"),
    "used-devices": CommandSpec("extract_used_devices_without_comments.py", "extract used devices without comments"),
    "extended-instructions": CommandSpec("extract_gx3_extended_instruction_knowledge.py", "extract instruction/device usage knowledge"),
    "dm-probe": CommandSpec("gx3_dm_probe.py", "decode *_DM.db device-memory initial/retained values"),
    "label-probe": CommandSpec("gx3_label_probe.py", "extract LabelData/SourceInfo labels, comments, arrays, and device assignments"),
    "mildb-probe": CommandSpec("gx3_mildb_probe.py", "extract *_MilDB.db rows and MIL device references"),
    "parse-gaps": CommandSpec("analyze_gx3_intermediate_parse_gaps.py", "summarize intermediate parse gaps"),
    "index-lite": CommandSpec("gx3_index_lite.py", "build/query lightweight SQLite index"),
    "lint": CommandSpec("gx3_lint.py", "static lint checks: coils, writers, alarms, unused/comment issues, links, math/type"),
    "xref": CommandSpec("gx3_xref.py", "full read/write cross-reference: build/where-used/downstream/export"),
    "alarm-map": CommandSpec("gx3_alarm_map.py", "alarm/fault inventory with trigger, hold, and reset conditions"),
    "exec-config": CommandSpec("gx3_exec_config.py", "program execution order, POU groups, and unit configuration"),
    "motion-rd77": CommandSpec("gx3_motion_rd77.py", "RD77 simple-motion buffer access map with official G labels"),
    "iut-probe": CommandSpec("gx3_iut_probe.py", "probe RD77 *.iut motion-setting container strings and paths"),
    "convertdata": CommandSpec("gx3_convertdata_probe.py", "probe ConvertData qpg and PouPCode record layout"),
    "semantic-diff": CommandSpec("gx3_semantic_diff.py", "rung-level diff between two projects (folders or .gx3)"),
    "interlock-check": CommandSpec("gx3_interlock.py", "check if two coils' ON conditions can be true simultaneously (static SAT)"),
    "dead-logic": CommandSpec("gx3_dead_logic.py", "constant-OFF branches, unread coils/words, SET without RST"),
    "program-map": CommandSpec("gx3_program_map.py", "LDDB -> POU name / program file / step mapping"),
    "timing-chart": CommandSpec("gx3_timing_chart.py", "generate generic handoff timing drafts from link-map and xref DBs"),
    "scan-order": CommandSpec("gx3_scan_order.py", "find writer/reader scan-order stale-read candidates"),
    "doctor": CommandSpec("gx3_doctor.py", "check CLI scripts, project root, indexes, xref DB, and link-map readiness"),
    "support-bundle": CommandSpec("gx3_support_bundle.py", "create a redacted support ZIP without ladder body data"),
    "synthetic-project": CommandSpec("gx3_synthetic_project.py", "generate a non-confidential synthetic GX3 fixture for tests and demos"),
    "reliability-report": CommandSpec("gx3_reliability_report.py", "one-page parse-gap and decoder coverage report"),
    "audit": CommandSpec("gx3_audit.py", "generate a read-only audit bundle: doctor, index, xref, lint, dead-logic"),
    "ai-context": CommandSpec("gx3_ai_context.py", "bundle compact evidence for GPT review or handoff"),
    "evidence-bundle": CommandSpec("gx3_ai_context.py", "alias of ai-context"),
    "tools": CommandSpec("gx3_tools.py", "extra GX3 inspection utilities"),
    "inspect": CommandSpec("gx3_tools.py", "classify readable/editable project files"),
    "sourceinfo": CommandSpec("gx3_tools.py", "dump SourceInfo.CAB entries"),
    "version": CommandSpec("gx3_tools.py", "show GX Works3 save/convert/write versions"),
    "ip-map": CommandSpec("gx3_tools.py", "extract registered IP address map"),
    "scon-map": CommandSpec("gx3_tools.py", "extract IAI/SCON axis maps and POS values"),
    "network-map": CommandSpec("gx3_network_map.py", "aggregate IP, CC-Link, SCON, and safety relationship map"),
    "coverage": CommandSpec("gx3_coverage.py", "report instruction/device knowledge covered by the CLI"),
    "instruction-coverage": CommandSpec("gx3_coverage.py", "alias: coverage instructions"),
    "device-coverage": CommandSpec("gx3_coverage.py", "alias: coverage devices"),
    "query-instruction": CommandSpec("gx3_tools.py", "search LadderBlocks.data by instruction/text"),
    "diff": CommandSpec("gx3_tools.py", "compare two .gx3 ZIP files"),
    "block-context": CommandSpec("gx3_tools.py", "show nearby ladder rows around a device occurrence"),
    "same-row": CommandSpec("gx3_tools.py", "show outputs and conditions that share rows with a device"),
    "signal-classify": CommandSpec("gx3_tools.py", "classify a device as pulse/hold/state/command"),
    "impact-add-nc": CommandSpec("gx3_tools.py", "show static impact of adding an NC contact"),
    "state-chain": CommandSpec("gx3_tools.py", "search state/mode chain candidates by text"),
}


def python_env(root: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Ensure the package is importable when running from a source checkout that
    # is not pip-installed. Harmless (site-packages parent) when installed.
    existing = env.get("PYTHONPATH", "")
    parent = str(PACKAGE_PARENT)
    if parent not in existing.split(os.pathsep):
        env["PYTHONPATH"] = parent + (os.pathsep + existing if existing else "")
    if root:
        resolved = str(resolve_project_root(root))
        env[ROOT_ENV] = resolved
        env[LEGACY_ROOT_ENV] = resolved
    return env


def module_argv(module: str, args: list[str]) -> list[str]:
    """argv to run a sibling command module as `python -m gx3cli.<module>`.

    Use this instead of building a path to a `.py` file: after packaging, running
    a package file as a loose script has no import context and fails.
    """
    return [sys.executable, "-m", f"{PACKAGE}.{module}", *args]


def cli_argv(args: list[str]) -> list[str]:
    """argv to re-invoke this dispatcher as `python -m gx3cli.gx3_cli ...`."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return module_argv("gx3_cli", args)


def run_module_in_process(module_name: str, args: list[str]) -> int:
    """Run a command module inside a PyInstaller-frozen gx3-cli.exe.

    Frozen apps cannot safely re-enter themselves with ``-m gx3cli.module``.
    Import the bundled module and call its main function with a temporary
    ``sys.argv`` instead.
    """

    module = importlib.import_module(f"{PACKAGE}.{module_name}")
    main_func = getattr(module, "main", None)
    if main_func is None:
        print(f"command module has no main(): {module_name}", file=sys.stderr)
        return 2
    old_argv = sys.argv[:]
    sys.argv = [module_name, *args]
    try:
        try:
            sig = inspect.signature(main_func)
            if len(sig.parameters) == 0:
                result = main_func()
            else:
                result = main_func(args)
        except TypeError:
            result = main_func()
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1
    finally:
        sys.argv = old_argv
    return int(result or 0)


def run_python_script(script: str, args: list[str], root: str | None = None) -> int:
    """Run a sibling command module as `python -m gx3cli.<module>`.

    ``script`` is kept as a filename (e.g. "gx3_lint.py") for backward-compatible
    command specs; the module name is derived from it. cwd is inherited from the
    caller so reports/index files land in the user's working directory.
    """
    module_name = script[:-3] if script.endswith(".py") else script
    if getattr(sys, "frozen", False):
        return run_module_in_process(module_name, args)
    if not (BASE_DIR / f"{module_name}.py").exists():
        print(f"missing command module: {module_name}", file=sys.stderr)
        return 2
    completed = subprocess.run(
        [sys.executable, "-m", f"{PACKAGE}.{module_name}", *args],
        env=python_env(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        if completed.returncode != 0 and "Traceback (most recent call last)" in completed.stderr:
            print(format_subprocess_failure(completed.stderr), file=sys.stderr)
        else:
            print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    return int(completed.returncode)


def root_from_index_db(db_path: str) -> str | None:
    path = Path(db_path)
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(str(path))
        row = con.execute("select value from meta where key='root'").fetchone()
        con.close()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None


def index_db_for_root(root: str) -> str:
    root_path = Path(root)
    name = root_path.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"
    return str(Path(".gx3_index") / f"{label}.sqlite")


def pop_option(argv: list[str], option: str) -> tuple[str | None, list[str]]:
    out: list[str] = []
    found: str | None = None
    i = 0
    while i < len(argv):
        if argv[i] == option and i + 1 < len(argv):
            found = argv[i + 1]
            i += 2
        elif argv[i].startswith(option + "="):
            found = argv[i].split("=", 1)[1]
            i += 1
        else:
            out.append(argv[i])
            i += 1
    return found, out


def normalize_root_value(value: str) -> str:
    return str(resolve_project_root(value))


def normalize_root_options(argv: list[str]) -> list[str]:
    out = list(argv)
    i = 0
    while i < len(out):
        if out[i] == "--root" and i + 1 < len(out):
            out[i + 1] = normalize_root_value(out[i + 1])
            i += 2
            continue
        if out[i].startswith("--root="):
            out[i] = "--root=" + normalize_root_value(out[i].split("=", 1)[1])
        i += 1
    return out


def normalize_project_arg(value: str) -> str:
    if "=" not in value:
        return normalize_root_value(value)
    label, root = value.split("=", 1)
    return f"{label}={normalize_root_value(root)}"


def normalize_project_options(argv: list[str]) -> list[str]:
    out = list(argv)
    i = 0
    while i < len(out):
        if out[i] == "--project" and i + 1 < len(out):
            out[i + 1] = normalize_project_arg(out[i + 1])
            i += 2
            continue
        if out[i].startswith("--project="):
            out[i] = "--project=" + normalize_project_arg(out[i].split("=", 1)[1])
        i += 1
    return out


def normalize_positional_project_root(command: str, argv: list[str]) -> list[str]:
    if command not in {"review", "lint"} or not argv or argv[0].startswith("-"):
        return argv
    out = list(argv)
    out[0] = normalize_root_value(out[0])
    return out


def format_subprocess_failure(stderr: str) -> str:
    last_line = next((line for line in reversed(stderr.splitlines()) if line.strip()), "unknown parser failure")
    return "\n".join(
        [
            "ERROR: GX3 command failed while reading this project.",
            f"Reason: {last_line}",
            "",
            "This may be an unsupported GX Works3 format or a parser coverage gap.",
            "",
            "1. See which rows could not be parsed:",
            "     gx3-cli parse-gaps --root <project>",
            "2. Build a redacted bundle (no ladder body, no device comments):",
            "     gx3-cli support-bundle --root <project>",
            "3. Report it, and attach that bundle:",
            f"     {REPORT_URL}",
            "",
            "Reports are what fix parser gaps. Do not paste project data,",
            "device comments, equipment names or addresses -- the bundle is",
            "redacted for exactly that reason.",
        ]
    )


def print_help() -> None:
    print(
        "\n".join(
            [
                "Project analysis CLI",
                "",
                "Usage:",
                "  gx3-cli list",
                "  gx3-cli context",
                "  gx3-cli quick-device DEVICE [extra trace args...]",
                "  gx3-cli <command> [command args...]",
                "  gx3-cli all-reports [--root ROOT] [--prefix PREFIX]",
                "  gx3-cli doctor --root ROOT",
                "  gx3-cli ai-context DEVICE --root ROOT --question \"...\"",
                "",
                "Common examples:",
                "  gx3-cli trace-device <DEVICE> --max-depth 3 --format text -o outputs\\device_trace.txt",
                "  gx3-cli trace-device <DEVICE> --strict-logic --max-depth 3 -o outputs\\device_strict_trace.txt",
                "  gx3-cli dependency-flow <DEVICE> -o outputs\\device_dependency_flow.md",
                "  gx3-cli ladder-diagram <DEVICE> --format markdown -o outputs\\device_ladder.md",
                "  gx3-cli matiec-st <DEVICE> -o outputs\\device_matiec.st",
                "  gx3-cli project-survey --output-dir outputs --prefix project_survey",
                "  gx3-cli external-inputs --output-dir outputs",
                "  gx3-cli review --prefix project_review",
                "  gx3-cli lint .\\_extracted_Project --xref-db .\\.gx3_index\\Project_xref.sqlite",
                "",
                "Use `gx3-cli list` to see available commands.",
            ]
        )
    )


def list_commands() -> None:
    width = max(len(name) for name in [*COMMANDS, "all-reports"])
    print("Available commands:")
    for name, spec in sorted(COMMANDS.items()):
        print(f"  {name:<{width}}  {spec.summary}")
    print(f"  {'all-reports':<{width}}  run the main read-only report generators in sequence")
    print(f"  {'context':<{width}}  print compact analysis entry points and ignore rules")
    print(f"  {'quick-device':<{width}}  compact strict trace for one device")
    print(f"  {'query-device':<{width}}  query one device from the lightweight SQLite index")
    print(f"  {'query-comment':<{width}}  search comments from the lightweight SQLite index")
    print(f"  {'query-external':<{width}}  query external/HMI/communication boundary devices")
    print(f"  {'query-cycle':<{width}}  list cycle/step coils from the lightweight SQLite index")
    print(f"  {'device-map':<{width}}  device-type usage ranges, density, and free gaps from the SQLite index")
    print("")
    print("Pass command arguments after the command name. Example:")
    print("  gx3-cli trace-device <DEVICE> --max-depth 3")


def latest_survey_index(output_dir: Path) -> Path:
    candidates = sorted(
        output_dir.glob("*_survey_index.md"),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return output_dir / f"{default_output_prefix('survey')}_index.md"


def survey_prefix_from_index(path: Path) -> str:
    suffix = "_index.md"
    return path.name[: -len(suffix)] if path.name.endswith(suffix) else default_output_prefix("survey")


def project_label_from_survey_prefix(prefix: str) -> str:
    return prefix[: -len("_survey")] if prefix.endswith("_survey") else prefix


def project_label_from_root(root: Path) -> str:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or project_label_from_survey_prefix(default_output_prefix("survey"))


def survey_index_for_root(output_dir: Path, root: Path) -> Path:
    """Return the survey index that matches the selected project root.

    Falling back to the newest survey makes the context command look valid
    while pointing at another PLC project. Return the expected per-root path
    instead so missing context is obvious and the rebuild command uses the
    correct prefix.
    """
    label = project_label_from_root(root)
    return output_dir / f"{label}_survey_index.md"


def latest_comm_prefix(output_dir: Path) -> str:
    candidates = sorted(
        output_dir.glob("*_comm_refresh_areas.csv"),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    if candidates:
        suffix = "_refresh_areas.csv"
        name = candidates[0].name
        return name[: -len(suffix)] if name.endswith(suffix) else default_comm_prefix()
    return default_comm_prefix()


def print_context() -> None:
    root = default_project_root(BASE_DIR)
    output_dir = BASE_DIR / "outputs"
    survey_index = survey_index_for_root(output_dir, root)
    survey_prefix = survey_prefix_from_index(survey_index)
    compact_context = output_dir / f"{survey_prefix}_context_compact.md"
    project_label = project_label_from_root(root)
    comm_prefix = latest_comm_prefix(output_dir)
    db_path = Path(".gx3_index") / f"{project_label}.sqlite"
    agent_guide = BASE_DIR / "AGENT_GX3.md"
    print("GX3 compact analysis context")
    print("")
    print(f"Target root: {root}")
    print("")
    print("Read first:")
    for path in [agent_guide, survey_index, compact_context]:
        status = "OK" if path.exists() else "missing"
        try:
            rel = path.relative_to(BASE_DIR)
        except ValueError:
            rel = path
        print(f"  - {rel} [{status}]")
    print("")
    print("Preferred commands:")
    print(f"  gx3-cli index-lite build --root {root} --out {db_path} --comm-dir outputs --comm-prefix {comm_prefix}")
    print("  gx3-cli query-device DEVICE")
    print("  gx3-cli quick-device DEVICE --ja")
    print("  gx3-cli trace-device DEVICE --strict-logic --compact --max-depth 4")
    print("  gx3-cli ladder-diagram DEVICE --format markdown")
    print(f"  gx3-cli project-survey --root {root} --output-dir outputs --prefix {survey_prefix} --comm-dir outputs --comm-prefix {comm_prefix} --compact-md-only")
    print("")
    print("Ignore unless explicitly requested:")
    print("  - _ARCHIVE_DELETE_CANDIDATES_*/")
    print("  - _KEEP_*/")
    print("  - __pycache__/")
    print("")
    print("Answer shape:")
    print("  active ON condition / hold condition / disabled branch / external boundary / uncertainty")


def run_quick_device(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gx3_cli.py quick-device")
    parser.add_argument("device", help="target device")
    parser.add_argument("--root", default=str(default_project_root(BASE_DIR)), help="extracted project folder")
    parser.add_argument("--max-depth", default="4", help="maximum upstream depth")
    parser.add_argument("--max-devices", default="300", help="maximum devices to trace")
    parser.add_argument("--ja", action="store_true", help="use Japanese headings")
    args, extra = parser.parse_known_args(argv)
    args.root = normalize_root_value(args.root)

    trace_args = [
        args.device,
        "--root",
        args.root,
        "--strict-logic",
        "--compact",
        "--max-depth",
        str(args.max_depth),
        "--max-devices",
        str(args.max_devices),
    ]
    if args.ja:
        trace_args.append("--ja")
    trace_args.extend(extra)
    return run_python_script(COMMANDS["trace-device"].script, trace_args, root=args.root)


def run_index_query(subcommand: str, argv: list[str]) -> int:
    root, argv = pop_option(argv, "--root")
    db, argv_without_db = pop_option(argv, "--db")
    if root:
        root = normalize_root_value(root)
    if db:
        argv = [*argv_without_db, "--db", db]
    elif root:
        argv = [*argv_without_db, "--db", index_db_for_root(root)]
    return run_python_script(COMMANDS["index-lite"].script, [subcommand, *argv])


# Commands whose script defines --root/--db on the MAIN parser before the
# subcommand. argparse rejects "build --root R" there, so gx3_cli reorders
# the options to the front: "xref build --root R" -> "--root R build".
GLOBAL_ROOT_BEFORE_SUBCOMMAND = {"xref", "alarm-map"}

INDEX_QUERY_COMMANDS = {
    "query-device": "device",
    "query-comment": "comment",
    "query-external": "external",
    "query-cycle": "cycle",
    "device-map": "device-map",
}

TOOLS_COMMANDS = {
    "tools",
    "inspect",
    "sourceinfo",
    "version",
    "ip-map",
    "scon-map",
    "query-instruction",
    "diff",
    "block-context",
    "same-row",
    "signal-classify",
    "impact-add-nc",
    "state-chain",
}


def hoist_global_options(argv: list[str]) -> list[str]:
    """Move --root/--db (given after the subcommand) before it."""
    out = list(argv)
    prefix: list[str] = []
    for option in ("--root", "--db"):
        value, out = pop_option(out, option)
        if value is not None:
            prefix.extend([option, value])
    return [*prefix, *out]


def run_root_command(command: str, spec: CommandSpec, argv: list[str]) -> int:
    if command in GLOBAL_ROOT_BEFORE_SUBCOMMAND:
        argv = hoist_global_options(argv)
    argv = normalize_project_options(normalize_root_options(argv))
    argv = normalize_positional_project_root(command, argv)
    db, stripped = pop_option(argv, "--db")
    if db and command in {"trace-device", "dependency-flow", "ladder-diagram", "matiec-st"}:
        root = root_from_index_db(db)
        if root and "--root" not in stripped and not any(a.startswith("--root=") for a in stripped):
            stripped.extend(["--root", root])
        return run_python_script(spec.script, stripped, root=root)
    return run_python_script(spec.script, argv)


def run_tools_command(command: str, argv: list[str]) -> int:
    tool_args = argv if command == "tools" else [command, *argv]
    return run_python_script("gx3_tools.py", tool_args)


def with_help_flag(argv: list[str]) -> list[str]:
    return argv if any(arg in {"-h", "--help"} for arg in argv) else [*argv, "--help"]


def run_all_reports(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="gx3_cli.py all-reports")
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--prefix", default=default_output_prefix("review"))
    parser.add_argument("--skip-comm", action="store_true")
    parser.add_argument("--skip-hmi", action="store_true")
    parser.add_argument("--skip-used-devices", action="store_true")
    parser.add_argument("--skip-extended-instructions", action="store_true")
    parser.add_argument("--skip-review", action="store_true")
    args = parser.parse_args(argv)
    args.root = normalize_root_value(args.root)

    steps: list[tuple[str, list[str]]] = []
    if not args.skip_comm:
        steps.append(("comm-refresh", ["--root", args.root]))
        steps.append(("comm-detail", ["--root", args.root]))
    if not args.skip_hmi:
        steps.append(("hmi-build-info", []))
    if not args.skip_used_devices:
        steps.append(("used-devices", []))
    if not args.skip_extended_instructions:
        steps.append(("extended-instructions", []))
    if not args.skip_review:
        steps.append(("review", [args.root, "--prefix", args.prefix]))

    for command, command_args in steps:
        spec = COMMANDS[command]
        print(f"\n==> {command}: {spec.summary}")
        code = run_python_script(spec.script, command_args, root=args.root)
        if code != 0:
            print(f"command failed: {command} (exit {code})", file=sys.stderr)
            return code
    print("\nall reports completed")
    return 0


def print_command_help(args: list[str]) -> int:
    if not args:
        print_help()
        return 0
    command = args[0]
    rest = args[1:]
    if command in INDEX_QUERY_COMMANDS:
        return run_python_script(COMMANDS["index-lite"].script, with_help_flag([INDEX_QUERY_COMMANDS[command], *rest]))
    if command == "quick-device":
        print("Usage: gx3-cli quick-device DEVICE [extra trace args...]")
        print("Runs trace-device with --strict-logic --compact and a bounded default depth.")
        return 0
    if command == "all-reports":
        print("Usage: gx3-cli all-reports [--root ROOT] [--prefix PREFIX] [--skip-comm] [--skip-hmi]")
        print("Runs the main read-only report generators in sequence.")
        return 0
    if command == "mcp-server":
        print("Usage: gx3-cli mcp-server [--version]")
        print("Starts the stdio MCP server.")
        return 0
    if command in TOOLS_COMMANDS:
        return run_tools_command(command, with_help_flag(rest))
    if command in COMMANDS:
        spec = COMMANDS[command]
        return run_python_script(spec.script, with_help_flag(rest))
    print(f"unknown command: {command}", file=sys.stderr)
    print("Run `gx3-cli list` for available commands.", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"--version", "-V"}:
        print(version_line("gx3-cli"))
        return 0
    if args and args[0] == "help":
        return print_command_help(args[1:])
    if not args or args[0] in {"-h", "--help"}:
        print_help()
        return 0

    command = args[0]
    rest = args[1:]
    try:
        if command == "list":
            list_commands()
            return 0
        if command == "context":
            print_context()
            return 0
        if command == "quick-device":
            return run_quick_device(rest)
        if command == "query-device":
            return run_index_query("device", rest)
        if command == "query-comment":
            return run_index_query("comment", rest)
        if command == "query-external":
            return run_index_query("external", rest)
        if command == "query-cycle":
            return run_index_query("cycle", rest)
        if command == "device-map":
            return run_index_query("device-map", rest)
        if command == "instruction-coverage":
            return run_python_script(COMMANDS["coverage"].script, normalize_root_options(hoist_global_options(["instructions", *rest])))
        if command == "device-coverage":
            return run_python_script(COMMANDS["coverage"].script, normalize_root_options(hoist_global_options(["devices", *rest])))
        if command == "all-reports":
            return run_all_reports(rest)
        if command == "mcp-server":
            from gx3cli.gx3_mcp_server import main as mcp_main

            return mcp_main(rest)
        if command in TOOLS_COMMANDS:
            return run_tools_command(command, normalize_root_options(rest))
        if command in COMMANDS:
            spec = COMMANDS[command]
            return run_root_command(command, spec, rest)
    except ProjectRootError as exc:
        print(project_root_error_message(str(exc)), file=sys.stderr)
        return 2

    print(f"unknown command: {command}", file=sys.stderr)
    print("Run `gx3-cli list` for available commands.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
