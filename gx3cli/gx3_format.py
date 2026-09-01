from __future__ import annotations

"""Shared GX Works3 project format inventory helpers."""

from dataclasses import dataclass
from pathlib import Path


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
        return any((self.fbddb_count, self.stdb_count, self.mildb_count))

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
            f"MilDB={self.mildb_count}" if self.mildb_count else "",
        ]
        detail = ", ".join(part for part in parts if part)
        return f"unsupported/non-ladder formats detected: {detail}" if detail else "no known program DB files"


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
