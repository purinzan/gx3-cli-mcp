from __future__ import annotations

"""Which input an answer came from, so two answers can be known to agree.

An index or a cross-reference records the path it was built from. A path is not
an identity: the folder behind it can be rebuilt, edited, or replaced by
another project entirely, and every answer afterwards is about a file nobody
opened. That failure has already happened here in another form -- three
commands ignored --root and answered about whatever they auto-detected -- and
it is worth catching by construction rather than by noticing.

The fingerprint is a hash over the files that decide what the analysis says:
the ladder, the comments, the labels, the unit configuration and the CPU
parameters. Each contributes its name, its size and its own hash, so a file
that changes, disappears or arrives changes the fingerprint.

Comments and communication settings are hashed alongside the ladder on purpose.
The question "did the logic, the comments and the communication information
come from the same input" is the one this has to be able to answer.
"""

import hashlib
from pathlib import Path


# The files an answer depends on. Anything not here can change without changing
# what the analysis says -- outputs, caches, the index itself.
ANALYSIS_INPUTS = (
    "*_LDDB.db",
    "*_DC.db",
    "*_MilDB.db",
    "*_StepInfo.db",
    "LabelData.db",
    "UnitConfig.dat",
    "CPU.PRM",
    "UNIT.PRM",
    "SYSTEM.PRM",
)

CHUNK = 1024 * 1024


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def input_files(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in ANALYSIS_INPUTS:
        found.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(found)


def fingerprint(root: Path) -> str:
    """A hash of the project's analysis inputs, or "" if there are none.

    An empty string rather than a hash of nothing: a folder with no ladder in
    it has no identity to compare, and pretending otherwise would make two
    unrelated empty folders look like the same input.
    """
    root = Path(root)
    files = input_files(root)
    if not files:
        return ""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(str(path.stat().st_size).encode("utf-8"))
        digest.update(file_digest(path).encode("utf-8"))
    return digest.hexdigest()


def short(value: str) -> str:
    return value[:12] if value else "(none)"


def mismatch_message(kind: str, path: Path, stored: str, actual: str, rebuild: str) -> str:
    return "\n".join(
        [
            f"{kind} was built from a different input: {path}",
            f"  built from: {short(stored)}   this project: {short(actual)}",
            "Answers taken from it would be about the other input.",
            f"Rebuild it: {rebuild}",
        ]
    )
