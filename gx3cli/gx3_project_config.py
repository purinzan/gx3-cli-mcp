from __future__ import annotations

"""Everything a project says about itself outside the ladder, in one run.

The ladder is half a project. The other half is which CPU, which units in which
slots, which devices a network writes without any rung touching them, what each
intelligent function module was set to, and what a motion module is being told
to do. Each of those is read by a different command, or by none, and knowing
which is which is most of the work.

So this is one command that reads all of it and, for each topic, says what it
found and what it cannot find and why. The "why" is the part that saves time:
a device memory file a project does not contain, an MES job list that is not in
the project at all, an encrypted body that will not open -- each of those is a
different thing from a gap in this tool, and reporting them the same way sends
someone looking for hours.

    gx3-cli project-config --root <project>
    gx3-cli project-config --root <project> --topic modules
    gx3-cli project-config --root <project> --format json -o config.json
"""

import argparse
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_exec_config import read_units
from gx3cli.gx3_format import build_format_inventory
from gx3cli.gx3_module_params import find_modules
from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root


TOPICS = ("cpu", "units", "network", "modules", "motion", "limits")

# Files whose contents are not readable, and the reason. Saying "encrypted"
# once is worth more than an afternoon spent proving it again.
UNREADABLE = {
    "_Project.txc": "encrypted project body (entropy 8.0); it does not open",
    "SYSTEM.PRM": "binary system parameters; no strings, format not decoded",
}


@dataclass
class Section:
    topic: str
    title: str
    lines: list[str] = field(default_factory=list)
    data: dict[str, object] = field(default_factory=dict)
    limits: list[str] = field(default_factory=list)


def utf16_strings(path: Path, minimum: int = 3) -> list[str]:
    if not path.exists():
        return []
    blob = path.read_bytes()
    out: list[str] = []
    for match in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % minimum, blob):
        try:
            text = match.group().decode("utf-16-le").strip()
        except UnicodeDecodeError:
            continue
        if text and text not in out:
            out.append(text)
    return out


def cpu_section(root: Path) -> Section:
    section = Section("cpu", "CPU and project")
    config = root / "Config.xml"
    model = ""
    if config.exists():
        match = re.search(r'Unit="([^"]+)"', config.read_text(encoding="utf-8-sig", errors="replace"))
        model = match.group(1) if match else ""
    inventory = build_format_inventory(root)
    section.data = {
        "cpu_model": model,
        "ladder_programs": inventory.lddb_count,
        "has_label_data": inventory.has_label_data,
        "non_ladder_programs": inventory.has_non_ladder_programs,
    }
    section.lines.append(f"CPU model            {model or '(not stated in Config.xml)'}")
    section.lines.append(f"ladder programs      {inventory.lddb_count}")
    section.lines.append(f"label data           {'yes' if inventory.has_label_data else 'no'}")
    if inventory.has_non_ladder_programs:
        section.lines.append(f"other languages      {inventory.unsupported_program_detail()}")
    section.lines.append("")
    section.lines.append("Program execution order is in CPU.PRM:  gx3-cli exec-config --root <project>")
    section.lines.append("Labels and their device assignment:     gx3-cli label-probe --root <project>")

    dm = list(root.glob("*_DM.db"))
    if dm:
        section.lines.append(f"device memory        {len(dm)} file(s):  gx3-cli dm-probe --root <project>")
    else:
        section.limits.append(
            "device memory: this project stores none (*_DM.db absent). Not a gap in the tool."
        )
    return section


def head_io_and_unit(raw: object) -> tuple[str, str]:
    """Spell a head I/O in hex, and the U number the ladder reaches it by.

    A unit's head I/O divided by 16 is that U number: head I/O 0x300 is
    U30\\G... . UnitConfig.dat stores it as a decimal number.
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""
    try:
        value = int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return text, ""
    return f"0x{value:X}", f"U{value // 16:X}"


def units_section(root: Path) -> Section:
    section = Section("units", "Unit configuration")
    units = read_units(root)
    section.data = {"units": units}
    if not units:
        section.limits.append("UnitConfig.dat is missing, so the unit list cannot be read")
        return section
    section.lines.append(f"{len(units)} units in UnitConfig.dat")
    section.lines.append("")
    section.lines.append(f"  {'base':<5} {'slot':<5} {'head I/O':<9} {'buffer':<7} unit")
    for unit in units:
        head_io, buffer_unit = head_io_and_unit(unit.get("head_io", ""))
        section.lines.append(
            f"  {str(unit.get('base', '')):<5} {str(unit.get('slot', '')):<5}"
            f" {head_io:<9} {buffer_unit:<7} {unit.get('unit_name', '')}"
        )
    section.lines.append("")
    section.lines.append("A unit's head I/O divided by 16 is the U number the ladder reaches its")
    section.lines.append("buffer memory by: head I/O 0x300 is U30\\G...")
    return section


def network_section(root: Path) -> Section:
    section = Section("network", "Addresses, connection method, refresh areas")
    addresses = []
    for path in sorted(root.glob("*.db")):
        if not re.fullmatch(r"\d+\.db", path.name):
            continue
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            model = con.execute(
                "select Data from DeviceInfo where Label='DeviceModel'"
            ).fetchone()
            for table in ("PARAM_BasicSetting", "PARAM_AppliSetting", "PARAM_AppliedSetting"):
                try:
                    rows = con.execute(f"select Label, Data from [{table}]").fetchall()
                except sqlite3.Error:
                    continue
                for label, value in rows:
                    text = str(value or "")
                    if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", text):
                        addresses.append(
                            {"unit": model[0] if model else path.name, "label": str(label), "address": text}
                        )
        finally:
            con.close()

    section.data = {"addresses": addresses}
    if addresses:
        section.lines.append("addresses set on module parameters:")
        for entry in addresses:
            section.lines.append(f"  {str(entry['unit']):<14} {entry['label']:<10} {entry['address']}")
    else:
        section.lines.append("no address is set on a module parameter database")

    connection = [s for s in utf16_strings(root / "UNIT.PRM") if
                  re.search(r"MELSOFT|SLMP|UDP|TCP|Connection|Host Station", s)]
    if connection:
        section.lines.append("")
        section.lines.append("connection methods named in UNIT.PRM:")
        for text in connection[:10]:
            section.lines.append(f"  {text}")
        section.data["connection_methods"] = connection

    section.lines.append("")
    section.lines.append("Refresh areas, remote stations and per-unit network settings:")
    section.lines.append("  gx3-cli comm-refresh --root <project>     -> project_comm_refresh_areas.csv")
    section.lines.append("  gx3-cli ip-map --root <project>")
    section.lines.append("  gx3-cli network-map --root <project> --index-db <index>")
    section.lines.append("")
    section.lines.append("A device inside a refresh area is written by the network, not by a rung.")
    section.lines.append("Feed the CSV to dead-logic so it stops reporting those as never written:")
    section.lines.append("  gx3-cli dead-logic --root <project> --db <xref>"
                         " --refresh-csv project_comm_refresh_areas.csv")
    return section


def modules_section(root: Path) -> Section:
    section = Section("modules", "Intelligent function modules")
    modules = find_modules(root)
    section.data = {
        "modules": [
            {
                "model": m.model,
                "base": m.identity.get("_BaseNo", ""),
                "slot": m.identity.get("_SlotNo", ""),
                "head_io": m.head_io,
                "buffer_unit": m.unit_number,
                "values": len(m.settings),
                "defaults_recorded": m.defaults_recorded,
                "catalogue_tables": len(m.catalogue_tables),
                "refresh_rows": m.refresh_rows,
            }
            for m in modules
        ]
    }
    if not modules:
        section.limits.append("no module parameter databases in this project")
        return section

    section.lines.append(f"{len(modules)} modules with a parameter database")
    section.lines.append("")
    section.lines.append(f"  {'unit':<14} {'buffer':<7} {'set':>4} {'catalogue':>10} {'refresh':>8}")
    for module in modules:
        section.lines.append(
            f"  {module.model:<14} {module.unit_number:<7} {len(module.settings):>6}"
            f" {len(module.catalogue_tables):>10} {module.refresh_rows:>8}"
        )
    section.lines.append("")
    section.lines.append("Every value a module parameter database holds:")
    section.lines.append("  gx3-cli module-params --root <project>")
    section.lines.append("  gx3-cli module-params --root <project> --unit <model>")
    section.lines.append("")
    section.lines.append("A table filed as BASICPARAMETER whose Prm3 is 257 is the parameter")
    section.lines.append("catalogue -- a descriptor saying a parameter exists, not its value. The")
    section.lines.append("values are under PARAM_*Setting, and DataArrayIndexX is the channel or axis.")

    section.limits.append(
        "the name behind BasePrm<n> is in the module profile GX Works3 installs,"
        " not in the project: the number and the value are readable, the meaning is not"
    )
    if not any(m.defaults_recorded for m in modules):
        section.limits.append(
            "DataDefault is empty in every row of every module database here, so which"
            " values a technician changed cannot be told from the project: what is"
            " readable is the values themselves"
        )
    mes = [m for m in modules if "MES" in m.model.upper()]
    if mes:
        section.limits.append(
            "MES job definitions, database connections and send items are not in the project:"
            " they are configured with the MES Interface tool and written to the module's SD card."
            " The database holds the I/O assignment and the switch settings only"
        )
    return section


def motion_section(root: Path) -> Section:
    section = Section("motion", "Simple motion")
    iut = sorted(root.glob("*.iut"))
    labels = 0
    label_db = ""
    for path in sorted(root.glob("*.db")):
        if not re.fullmatch(r"\d+\.db", path.name):
            continue
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            count = con.execute("select count(*) from _UnitLabel").fetchone()[0]
        except sqlite3.Error:
            # No label table in this module; not every module has one.
            continue
        finally:
            con.close()
        if count > labels:
            # Several modules carry a label table; the motion one is by far the
            # largest, and taking whichever came last named a serial module.
            labels, label_db = count, path.name

    section.data = {"iut_files": len(iut), "buffer_labels": labels, "label_db": label_db}
    if not iut and not labels:
        section.lines.append("no motion module in this project")
        return section

    if labels:
        section.lines.append(f"{labels} buffer memory labels in {label_db}")
        section.lines.append("  G10 and up name the axis parameters:"
                             " RD77.stnAxPrm[0].udSpeedLimitValue and the rest")
    section.lines.append("")
    section.lines.append("What the ladder tells the module, with those labels resolved:")
    section.lines.append("  gx3-cli xref build --root <project>")
    section.lines.append("  gx3-cli motion-rd77 --root <project>")

    if iut:
        size = iut[0].stat().st_size
        section.lines.append("")
        section.lines.append(f"axis parameter container: {iut[0].name} ({size:,} bytes)")
        section.lines.append("  gx3-cli iut-probe --root <project>")
        section.limits.append(
            "axis parameter values (homing method, acceleration times, soft limits) are in"
            f" the .iut and are not decoded. It is not encrypted -- records are a u16 length"
            " and a UTF-16 name -- but pairing a value with a parameter needs one example"
            " checked against the GX Works3 screen, and a wrong soft limit is worse than none"
        )
    return section


def limits_section(root: Path, sections: list[Section]) -> Section:
    section = Section("limits", "What cannot be read, and why")
    for name, reason in UNREADABLE.items():
        if (root / name).exists():
            section.lines.append(f"  {name:<18} {reason}")
    for other in sections:
        for limit in other.limits:
            section.lines.append(f"  [{other.topic}] {limit}")
    if not section.lines:
        section.lines.append("  nothing in this project is in that state")
    section.data = {"limits": [line.strip() for line in section.lines]}
    return section


def collect(root: Path, topics: tuple[str, ...]) -> list[Section]:
    builders = {
        "cpu": cpu_section,
        "units": units_section,
        "network": network_section,
        "modules": modules_section,
        "motion": motion_section,
    }
    sections = [builders[topic](root) for topic in topics if topic in builders]
    if "limits" in topics:
        sections.append(limits_section(root, sections))
    return sections


def as_text(root: Path, sections: list[Section]) -> list[str]:
    out = [f"project: {root}"]
    for section in sections:
        out.append("")
        out.append(f"== {section.title}")
        out.extend(f"  {line}" if line else "" for line in section.lines)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read everything a project says about itself outside the ladder."
    )
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument(
        "--topic",
        action="append",
        choices=TOPICS,
        help="report only this topic (repeatable; default: all)",
    )
    add_format_argument(parser, choices=("text", "json"), json_shorthand=False)
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"project root not found: {root}")
        return 1
    topics = tuple(args.topic) if args.topic else TOPICS
    sections = collect(root, topics)

    return emit(
        args,
        text=lambda: as_text(root, sections),
        data=lambda: {
            "root": str(root),
            "sections": [
                {"topic": s.topic, "title": s.title, "data": s.data, "limits": s.limits}
                for s in sections
            ],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
