from __future__ import annotations

"""Which of the commands are worth running on *this* project.

There are sixty-odd commands. `list` prints all of them and `--help` groups
them, but neither says which ones will find anything here: a project with no
communication units has nothing for comm-detail, and a project written with
labels needs label-probe where a device-based one does not.

So this looks at what the project actually contains -- which files are present,
whether an index has been built, how the rungs came out -- and names the
commands that follow from that, with the reason attached. A suggestion without
its reason is just a shorter list.

It only reports what it can see. A command is left out when the evidence for it
is absent, not when it is judged unlikely to help, so "not listed" means "no
sign of it in this project" rather than "not worth trying".
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.gx3_workspace import locate


@dataclass(frozen=True)
class Suggestion:
    command: str
    reason: str
    # Lower runs first. 0 is "do this before anything else".
    order: int = 5

    def to_line(self, width: int) -> str:
        return f"  {self.command:<{width}}  {self.reason}"


@dataclass
class Evidence:
    """What the project root shows, before any command has been run."""

    root: Path
    ladder_dbs: int = 0
    step_dbs: int = 0
    label_db: bool = False
    comment_db: bool = False
    device_memory: bool = False
    mil_db: bool = False
    parameters: int = 0
    convertdata: bool = False
    motion: bool = False
    hmi: bool = False
    index_built: bool = False
    xref_built: bool = False
    index_note: str = ""


def gather(root: Path) -> Evidence:
    root = Path(root)
    names = [p.name for p in root.iterdir()] if root.is_dir() else []
    lower = [n.lower() for n in names]

    def any_suffix(*suffixes: str) -> int:
        return sum(1 for n in lower if n.endswith(suffixes))

    evidence = Evidence(root=root)
    evidence.ladder_dbs = sum(1 for n in lower if n.endswith("_lddb.db"))
    evidence.step_dbs = sum(1 for n in lower if n.endswith("_stepinfo.db"))
    evidence.label_db = "labeldata.db" in lower
    evidence.comment_db = any(n.endswith("_dc.db") for n in lower)
    evidence.device_memory = any(n.endswith("_dm.db") for n in lower)
    evidence.mil_db = any(n.endswith("_mildb.db") for n in lower)
    evidence.parameters = any_suffix(".w3pa", ".prm")
    evidence.convertdata = any("convertdata" in n for n in lower)
    evidence.motion = any_suffix(".iut")
    evidence.hmi = any_suffix(".gtx")

    # An index next to the project, one in the current directory and one built
    # by an older version are three different answers to "is it built". The
    # workspace knows which file a command would actually read, and whether it
    # was built from this input -- a stale one is not "built" for this purpose.
    workspace = locate(root)
    evidence.index_built = workspace.index.usable
    evidence.xref_built = workspace.xref.usable
    evidence.index_note = workspace.xref.detail if not workspace.xref.usable else ""
    return evidence


def suggest(evidence: Evidence) -> list[Suggestion]:
    out: list[Suggestion] = []

    def add(command: str, reason: str, order: int = 5) -> None:
        out.append(Suggestion(command, reason, order))

    add("doctor", "confirm the project reads before trusting anything else", 0)

    if evidence.ladder_dbs:
        rungs = f"{evidence.ladder_dbs} ladder program(s)"
        if not evidence.xref_built:
            why = evidence.index_note or "no cross-reference yet"
            add("workspace --prepare", f"{rungs}, {why}; most commands need one", 1)
        add("metrics", f"{rungs}: size per program, and where the logic is concentrated", 2)
        add("rung-text", "read the whole program as one line per rung", 3)
        add("lint", "static checks over the rungs: coils, writers, widths, operand types", 4)
        add("interlock-check", "conditions that hold an output off", 5)
        add("dead-logic", "rungs that can never become true", 6)
    else:
        add("inspect", "no ladder databases here; see what the container does hold", 1)

    if evidence.label_db:
        add(
            "label-probe",
            "LabelData.db present: this project is written with labels, so names come from there",
            3,
        )
    if evidence.comment_db:
        add("device-dictionary", "device comments present: export them with their usage", 6)
    else:
        add("used-devices", "no comment database found; list the devices that carry no comment", 6)

    if evidence.device_memory:
        add("dm-probe", "device-memory database present: initial and retained values", 7)
    if evidence.mil_db:
        add("mildb-probe", "MilDB present: MIL device references", 8)
    if evidence.parameters:
        add(
            "comm-refresh",
            f"{evidence.parameters} parameter file(s): communication units and refresh areas",
            5,
        )
        add("network-map", "parameters present: build the network picture", 6)
    if evidence.motion:
        add("motion-rd77", "RD77 motion settings present", 7)
    if evidence.hmi:
        add("gtx-probe", "a GT Designer3 project sits alongside; probe the HMI side", 7)
    if evidence.convertdata:
        add("sourceinfo", "ConvertData present: source and label metadata", 8)

    add("parse-gaps", "how much of the intermediate data was understood", 9)
    return sorted(out, key=lambda s: (s.order, s.command))


def render(evidence: Evidence, suggestions: list[Suggestion]) -> list[str]:
    lines = [f"project: {evidence.root}", ""]
    seen = [
        ("ladder programs", evidence.ladder_dbs or ""),
        ("step info", evidence.step_dbs or ""),
        ("labels", "LabelData.db" if evidence.label_db else ""),
        ("comments", "yes" if evidence.comment_db else ""),
        ("device memory", "yes" if evidence.device_memory else ""),
        ("parameters", evidence.parameters or ""),
        ("motion", "yes" if evidence.motion else ""),
        ("HMI", "yes" if evidence.hmi else ""),
        ("cross-reference", "built" if evidence.xref_built else (evidence.index_note or "not built")),
    ]
    found = [(name, value) for name, value in seen if value != ""]
    if found:
        lines.append("what this project contains")
        width = max(len(name) for name, _ in found)
        for name, value in found:
            lines.append(f"  {name:<{width}}  {value}")
        lines.append("")

    lines.append("start with")
    width = max((len(s.command) for s in suggestions), default=0)
    for suggestion in suggestions:
        lines.append(suggestion.to_line(width))
    lines.append("")
    lines.append("`gx3-cli list` shows every command, including the ones this project has no sign of.")
    return lines


def to_json(evidence: Evidence, suggestions: list[Suggestion]) -> dict[str, Any]:
    return {
        "root": str(evidence.root),
        "evidence": {
            "ladder_programs": evidence.ladder_dbs,
            "step_info": evidence.step_dbs,
            "labels": evidence.label_db,
            "comments": evidence.comment_db,
            "device_memory": evidence.device_memory,
            "mil_db": evidence.mil_db,
            "parameter_files": evidence.parameters,
            "convertdata": evidence.convertdata,
            "motion": evidence.motion,
            "hmi": evidence.hmi,
            "index_built": evidence.index_built,
            "xref_built": evidence.xref_built,
            "index_note": evidence.index_note,
        },
        "suggestions": [
            {"command": s.command, "reason": s.reason, "order": s.order} for s in suggestions
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Suggest which commands to run, from what this project contains."
    )
    parser.add_argument("--root", default=str(default_project_root()))
    add_format_argument(parser, json_shorthand=False)
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"not a project directory: {root}")
        return 1

    evidence = gather(root)
    suggestions = suggest(evidence)
    return emit(
        args,
        text=lambda: render(evidence, suggestions),
        data=lambda: to_json(evidence, suggestions),
    )


if __name__ == "__main__":
    raise SystemExit(main())
