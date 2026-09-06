from __future__ import annotations

"""Shared GX Works3 project format inventory and partial ST-reference helpers."""

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_analysis_state import (
    DISCOVERY,
    UNSUPPORTED,
    AnalysisState,
    checked,
)
from gx3cli.gx3_device_name import split_device


DB_PATTERNS = {
    "lddb": "*_LDDB.db",
    "fbddb": "*_FBDDB.db",
    "stdb": "*_STDB.db",
    "mildb": "*_MilDB.db",
    "dm": "*_DM.db",
    "dc": "*_DC.db",
    "stepinfo": "*_StepInfo.db",
}


@dataclass(frozen=True)
class GX3FormatInventory:
    root: Path
    lddb_count: int = 0
    fbddb_count: int = 0
    stdb_count: int = 0
    mildb_count: int = 0
    dm_count: int = 0
    dc_count: int = 0
    stepinfo_count: int = 0
    has_cpu_prm: bool = False
    has_label_data: bool = False

    @property
    def has_ladder(self) -> bool:
        return self.lddb_count > 0

    @property
    def has_non_ladder_programs(self) -> bool:
        # A MilDB accompanies a ladder program -- every project here has one
        # per LDDB -- so counting it made every project report a non-ladder
        # program it does not have, and "unsupported formats detected" was
        # printed for projects that are ladder from end to end. FBD and ST are
        # the languages this cannot fully read.
        return any((self.fbddb_count, self.stdb_count))

    @property
    def has_known_program_db(self) -> bool:
        return self.has_ladder or self.has_non_ladder_programs

    def counts(self) -> dict[str, int]:
        return {
            "LDDB": self.lddb_count,
            "FBDDB": self.fbddb_count,
            "STDB": self.stdb_count,
            "MilDB": self.mildb_count,
            "DM": self.dm_count,
            "DC": self.dc_count,
            "StepInfo": self.stepinfo_count,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            **self.counts(),
            "CPU.PRM": self.has_cpu_prm,
            "LabelData.db": self.has_label_data,
            "has_ladder": self.has_ladder,
            "has_non_ladder_programs": self.has_non_ladder_programs,
            "has_known_program_db": self.has_known_program_db,
        }

    def detail(self) -> str:
        parts = [f"{name}={count}" for name, count in self.counts().items() if count]
        if self.has_cpu_prm:
            parts.append("CPU.PRM")
        if self.has_label_data:
            parts.append("LabelData.db")
        return ", ".join(parts) if parts else "no known GX3 DB files"

    def unsupported_program_detail(self) -> str:
        parts = [
            f"FBDDB={self.fbddb_count}" if self.fbddb_count else "",
            f"STDB={self.stdb_count}" if self.stdb_count else "",
        ]
        detail = ", ".join(part for part in parts if part)
        return f"unsupported/non-ladder formats detected: {detail}" if detail else "no known program DB files"


def unsupported_programs(inventory: "GX3FormatInventory") -> AnalysisState:
    """Programs in a form this tool does not fully read, if the project holds any."""
    kinds = []
    if inventory.fbddb_count:
        kinds.append(f"{inventory.fbddb_count} FBD")
    if inventory.stdb_count:
        kinds.append(f"{inventory.stdb_count} ST")
    if not kinds:
        return checked()
    return AnalysisState(
        UNSUPPORTED,
        reason=(
            f"{', '.join(kinds)} program(s) here are in a form this does not fully read; "
            "the numbers below cover the ladder programs plus any conservatively extracted ST references"
        ),
        next_step="review unsupported ST/FBD syntax in GX Works3; extracted references are partial evidence only",
        stage=DISCOVERY,
    )


def build_format_inventory(root: Path) -> GX3FormatInventory:
    root = Path(root)
    counts = {name: len(list(root.glob(pattern))) for name, pattern in DB_PATTERNS.items()}
    return GX3FormatInventory(
        root=root,
        lddb_count=counts["lddb"],
        fbddb_count=counts["fbddb"],
        stdb_count=counts["stdb"],
        mildb_count=counts["mildb"],
        dm_count=counts["dm"],
        dc_count=counts["dc"],
        stepinfo_count=counts["stepinfo"],
        has_cpu_prm=(root / "CPU.PRM").exists(),
        has_label_data=(root / "LabelData.db").exists(),
    )


# ----- Partial Structured Text reference bridge -------------------------------

# This is intentionally not a full IEC ST grammar. A supported statement is a
# simple assignment with no call/control-flow/array syntax. Anything outside
# that subset is evidence of a gap, not an invitation to guess references.
_ST_ASSIGNMENT_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.]*|[A-Za-z]+[0-9A-Fa-f]+)\s*:=\s*(.*?)\s*;?\s*$",
    re.DOTALL,
)
_ST_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*\b")
_ST_DEVICE_RE = re.compile(r"\b[A-Za-z]+[0-9A-Fa-f]+\b")
_ST_UNSUPPORTED_CONTROL_RE = re.compile(
    r"\b(?:IF|THEN|ELSIF|ELSE|END_IF|CASE|OF|END_CASE|FOR|TO|BY|DO|END_FOR|"
    r"WHILE|END_WHILE|REPEAT|UNTIL|END_REPEAT|RETURN|EXIT|JMP|LABEL)\b",
    re.IGNORECASE,
)
_ST_STRING_RE = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
_ST_RESERVED = {
    "TRUE", "FALSE", "AND", "OR", "XOR", "NOT", "MOD", "DIV",
    "BOOL", "BYTE", "WORD", "DWORD", "INT", "DINT", "UINT", "UDINT",
    "REAL", "LREAL", "TIME", "DATE", "STRING", "WSTRING",
}


@dataclass(frozen=True)
class STReference:
    symbol: str
    access: str
    source_kind: str
    source_file: str
    source_location: str
    pou: str = ""
    statement_index: int = 0
    coverage: str = "supported"
    reason: str = ""

    @property
    def is_device(self) -> bool:
        return split_device(self.symbol) is not None


@dataclass
class STSource:
    source_kind: str
    source_file: str
    source_location: str
    text: str
    pou: str = ""
    coverage: str = "supported"
    reasons: list[str] = field(default_factory=list)
    references: list[STReference] = field(default_factory=list)


def _st_symbols(expression: str) -> list[str] | None:
    """Return symbols from a supported RHS, or None when syntax is out of scope."""
    cleaned = _ST_STRING_RE.sub(" ", expression)
    # Function calls are intentionally non-goals. Do not harvest arguments out
    # of them: that would turn an unsupported statement into guessed evidence.
    if re.search(r"\b[A-Za-z_][A-Za-z0-9_.]*\s*\(", cleaned):
        return None
    if any(ch in cleaned for ch in "[]{}"):
        return None
    out: list[str] = []
    occupied: list[tuple[int, int]] = []
    for match in _ST_DEVICE_RE.finditer(cleaned):
        token = match.group(0)
        if split_device(token) is not None:
            out.append(token.upper())
            occupied.append(match.span())
    for match in _ST_IDENTIFIER_RE.finditer(cleaned):
        if any(a <= match.start() < b for a, b in occupied):
            continue
        token = match.group(0)
        if token.upper() in _ST_RESERVED:
            continue
        # Decimal/hex-looking tokens are not identifiers because the regex
        # starts with a letter; enum/type constants remain labels conservatively.
        out.append(token)
    # Preserve source order while deduplicating within one statement.
    return list(dict.fromkeys(out))


def parse_st_text(
    text: str,
    *,
    source_kind: str,
    source_file: str,
    source_location: str,
    pou: str = "",
) -> STSource:
    """Extract only references proved by simple ST assignments.

    Unsupported statements make the source partial. No symbol from an
    unsupported statement is materialized, which is the core safety contract
    for this bridge.
    """
    source = STSource(source_kind, source_file, source_location, text, pou=pou)
    # Comments are removed before statement splitting so commented-out devices
    # cannot become references. Nested block comments are outside this subset.
    cleaned = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.DOTALL)
    cleaned = re.sub(r"//[^\r\n]*", " ", cleaned)
    statements = [part.strip() for part in cleaned.split(";") if part.strip()]
    if not statements and cleaned.strip():
        statements = [cleaned.strip()]

    for index, statement in enumerate(statements, 1):
        if _ST_UNSUPPORTED_CONTROL_RE.search(statement):
            source.coverage = "partial"
            source.reasons.append(f"statement {index}: control-flow syntax is outside the partial ST parser")
            continue
        match = _ST_ASSIGNMENT_RE.match(statement)
        if not match:
            source.coverage = "partial"
            source.reasons.append(f"statement {index}: unsupported ST statement")
            continue
        destination, rhs = match.groups()
        if split_device(destination) is not None:
            destination = destination.upper()
        rhs_symbols = _st_symbols(rhs)
        if rhs_symbols is None:
            source.coverage = "partial"
            source.reasons.append(f"statement {index}: function/array syntax is outside the partial ST parser")
            continue
        source.references.append(
            STReference(
                destination,
                "write",
                source_kind,
                source_file,
                source_location,
                pou,
                index,
                source.coverage,
            )
        )
        for symbol in rhs_symbols:
            source.references.append(
                STReference(
                    symbol,
                    "read",
                    source_kind,
                    source_file,
                    source_location,
                    pou,
                    index,
                    source.coverage,
                )
            )
    if source.reasons:
        source.coverage = "partial"
        # References parsed before/after a gap stay valid but are partial
        # evidence about the source as a whole.
        source.references = [
            STReference(
                r.symbol, r.access, r.source_kind, r.source_file,
                r.source_location, r.pou, r.statement_index,
                "partial", "; ".join(source.reasons),
            )
            for r in source.references
        ]
    return source


def _decode_candidate_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, (bytes, bytearray)):
        return ""
    blob = bytes(value)
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        try:
            text = blob.decode(encoding)
        except UnicodeError:
            continue
        if ":=" in text or _ST_UNSUPPORTED_CONTROL_RE.search(text):
            return text
    return ""


def enumerate_st_sources(root: Path, pou_by_file: dict[str, str] | None = None) -> list[STSource]:
    """Find readable ST text in STDBs without assuming one GX Works3 schema.

    STDB schemas vary. The bridge therefore enumerates SQLite text/blob cells
    and accepts only cells that look like ST (assignment/control syntax). The
    table/row/column locator is kept as evidence. A database that cannot be
    inspected is represented as a partial source instead of disappearing.
    """
    root = Path(root)
    pou_by_file = pou_by_file or {}
    sources: list[STSource] = []
    for path in sorted(root.glob("*_STDB.db")):
        pou = pou_by_file.get(path.name, "")
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            sources.append(STSource("st", path.name, "database", "", pou, "partial", [f"cannot open: {exc}"]))
            continue
        found = False
        try:
            tables = [str(row[0]) for row in con.execute("select name from sqlite_master where type='table' order by name")]
            for table in tables:
                columns = [str(row[1]) for row in con.execute(f"pragma table_info([{table}])")]
                if not columns:
                    continue
                try:
                    rows = con.execute(f"select rowid as __rowid__, * from [{table}]")
                except sqlite3.Error:
                    continue
                for row in rows:
                    rowid = int(row["__rowid__"])
                    for column in columns:
                        text = _decode_candidate_text(row[column])
                        if ":=" not in text and not _ST_UNSUPPORTED_CONTROL_RE.search(text):
                            continue
                        found = True
                        sources.append(
                            parse_st_text(
                                text,
                                source_kind="st",
                                source_file=path.name,
                                source_location=f"{table}:rowid={rowid}:{column}",
                                pou=pou,
                            )
                        )
        except sqlite3.Error as exc:
            sources.append(STSource("st", path.name, "database", "", pou, "partial", [f"cannot inspect schema: {exc}"]))
        finally:
            con.close()
        if not found and not any(s.source_file == path.name for s in sources):
            sources.append(
                STSource(
                    "st", path.name, "database", "", pou, "partial",
                    ["STDB present but no supported text storage was identified"],
                )
            )
    return sources


def enumerate_inline_st_sources(
    rows_by_db: dict[str, list[object]],
    pou_by_file: dict[str, str] | None = None,
) -> list[STSource]:
    """Find inline-ST text carried inside ladder row payloads when visible.

    The raw ladder encoding itself is not ST, so a row is considered only when
    its payload contains ``:=``. This does not guess at opaque inline-ST
    containers; such projects remain partial via the format/parse-gap state.
    """
    pou_by_file = pou_by_file or {}
    sources: list[STSource] = []
    for lddb, rows in rows_by_db.items():
        pou = pou_by_file.get(lddb, "")
        for raw in rows:
            try:
                data = str(raw["data"])
                pos = int(float(raw["pos"]))
            except (KeyError, TypeError, ValueError):
                continue
            if ":=" not in data:
                continue
            # Keep only line-like fragments containing assignment text rather
            # than feeding the whole GX ladder serialization to the ST parser.
            fragments = [fragment.strip() for fragment in re.split(r"[\r\n]", data) if ":=" in fragment]
            for n, fragment in enumerate(fragments, 1):
                sources.append(
                    parse_st_text(
                        fragment,
                        source_kind="inline-st",
                        source_file=lddb,
                        source_location=f"pos={pos}:fragment={n}",
                        pou=pou,
                    )
                )
    return sources
