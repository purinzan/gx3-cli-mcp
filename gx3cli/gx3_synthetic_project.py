from __future__ import annotations

"""Generate non-confidential GX3-like fixtures for tests, demos, and docs."""

import argparse
import shutil
import sqlite3
import zipfile
from pathlib import Path

from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.review_gx3_project import DEVICE_CODE_BY_TYPE


def _create_ladder_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        create table LadderBlocks (
            id text,
            pos real,
            blocktype integer,
            data text,
            rowsize integer,
            translated integer,
            ConvTarget integer
        )
        """
    )
    rows = []
    title = ":st{m=0:dim=0}Emergency stop origin return demo"
    rows.append(("_guid/00000000-0000-0000-0000-000000000010", 0.0, 1, title, len(title), 0, 0))
    data, rowsize, _ = generate_rung(
        {"device": "X48"},
        {"type": "coil", "device": "M55"},
    )
    rows.append(("_guid/00000000-0000-0000-0000-000000000011", 5.0, 0, data, rowsize, 0, 0))
    data, rowsize, _ = generate_rung(
        {"and": [{"device": "X16"}, {"not": {"device": "M55"}}]},
        {"type": "coil", "device": "M100"},
    )
    rows.append(("_guid/00000000-0000-0000-0000-000000000012", 10.0, 0, data, rowsize, 0, 0))
    data, rowsize, _ = generate_rung(
        {"and": [{"device": "M100"}, {"device": "X32"}]},
        {"type": "coil", "device": "Y16"},
    )
    rows.append(("_guid/00000000-0000-0000-0000-000000000013", 20.0, 0, data, rowsize, 0, 0))
    con.executemany("insert into LadderBlocks values (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def _create_comment_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("create table DEVICE_DATA(SEQ integer, DevCode integer, ExtCode integer, ExtNo integer, DevNoLow integer, BitNo integer)")
    con.execute("create table COMMENT_DATA(DeviceSEQ integer, CmtNo integer, CmtData text, DelFlag integer)")
    devices = [
        (1, "X", 0x30, "Synthetic origin inhibit request input"),
        (2, "X", 0x10, "Synthetic emergency stop input"),
        (3, "M", 55, "Synthetic origin-return inhibit"),
        (4, "M", 100, "Synthetic origin-return request"),
        (5, "X", 0x20, "Synthetic servo ready input"),
        (6, "Y", 0x10, "Synthetic origin-return command"),
    ]
    con.executemany(
        "insert into DEVICE_DATA values (?, ?, 0, 0, ?, 0)",
        [(seq, DEVICE_CODE_BY_TYPE[dev_type], dev_no) for seq, dev_type, dev_no, _comment in devices],
    )
    con.executemany(
        "insert into COMMENT_DATA values (?, 5, ?, 0)",
        [(seq, comment) for seq, _dev_type, _dev_no, comment in devices],
    )
    con.commit()
    con.close()


def create_synthetic_project(root: Path, overwrite: bool = False) -> Path:
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {root}")
        if root.is_dir():
            shutil.rmtree(root)
        else:
            root.unlink()
    root.mkdir(parents=True)
    (root / "UnitConfig.dat").write_text("synthetic unit config\n", encoding="utf-8")
    (root / "CPU.PRM").write_text("synthetic cpu parameters\n", encoding="utf-8")
    (root / "LabelData.db").write_bytes(b"")
    _create_ladder_db(root / "001_LDDB.db")
    _create_comment_db(root / "001_DC.db")
    return root


def create_synthetic_gx3_archive(path: Path, overwrite: bool = False) -> Path:
    work = path.with_suffix("")
    create_synthetic_project(work, overwrite=overwrite)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {path}")
        path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(work.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(work).as_posix())
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic, non-confidential GX3-like fixture.")
    parser.add_argument("output", type=Path, help="folder path or .gx3 archive path")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.output.suffix.lower() == ".gx3":
        created = create_synthetic_gx3_archive(args.output, overwrite=args.overwrite)
    else:
        created = create_synthetic_project(args.output, overwrite=args.overwrite)
    print(f"synthetic project created: {created}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
