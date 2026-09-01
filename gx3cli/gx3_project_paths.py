from __future__ import annotations

import os
import hashlib
import shutil
import sqlite3
import zipfile
from pathlib import Path
from dataclasses import dataclass


DEFAULT_OUTPUT_PREFIX = "project"
ROOT_ENV = "PROJECT_ROOT"
LEGACY_ROOT_ENV = "GX3_ROOT"
OUTPUT_PREFIX_ENV = "PROJECT_OUTPUT_PREFIX"
LEGACY_OUTPUT_PREFIX_ENV = "GX3_OUTPUT_PREFIX"
COMM_PREFIX_ENV = "PROJECT_COMM_PREFIX"
LEGACY_COMM_PREFIX_ENV = "GX3_COMM_PREFIX"


class ProjectRootError(ValueError):
    """Raised when a project root or .gx3 archive cannot be prepared."""


@dataclass(frozen=True)
class ConvertDataEntry:
    """A ConvertData member that may be extracted with Windows separators.

    Some .gx3 archives store member names with backslashes. On POSIX,
    ``zipfile.extractall`` preserves those backslashes as literal filename
    characters instead of creating nested directories, so callers cannot rely on
    ``root / "ConvertData" / id / name`` existing.
    """

    program_id: str
    member_name: str
    path: Path


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def is_extracted_gx3_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    return (
        (path / "UnitConfig.dat").exists()
        or any(path.glob("*_LDDB.db"))
        or any(path.glob("*_FBDDB.db"))
        or any(path.glob("*_DC.db"))
    )


def iter_convertdata_entries(root: Path, member_name: str | None = None) -> list[ConvertDataEntry]:
    """Return ConvertData files from normal or backslash-preserved layouts."""

    entries: list[ConvertDataEntry] = []
    convert = root / "ConvertData"
    if convert.is_dir():
        for path in sorted(convert.glob("*/*")):
            if not path.is_file():
                continue
            if member_name and path.name != member_name:
                continue
            entries.append(ConvertDataEntry(path.parent.name, path.name, path))

    prefix = "ConvertData\\"
    for path in sorted(root.iterdir() if root.is_dir() else []):
        if not path.is_file() or not path.name.startswith(prefix):
            continue
        parts = path.name.split("\\")
        if len(parts) != 3 or not parts[1] or not parts[2]:
            continue
        if member_name and parts[2] != member_name:
            continue
        entries.append(ConvertDataEntry(parts[1], parts[2], path))

    seen: set[Path] = set()
    unique: list[ConvertDataEntry] = []
    for entry in entries:
        if entry.path in seen:
            continue
        seen.add(entry.path)
        unique.append(entry)
    return unique


def convertdata_path(root: Path, program_id: str, member_name: str) -> Path:
    """Resolve one ConvertData member across supported extraction layouts."""

    normal = root / "ConvertData" / program_id / member_name
    if normal.exists():
        return normal
    flat = root / f"ConvertData\\{program_id}\\{member_name}"
    if flat.exists():
        return flat
    return normal


def is_gx3_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".gx3"


def gx3_archive_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_root_for(path: Path, digest: str) -> Path:
    return path.parent / ".gx3_cache" / digest


def _project_root_inside_cache(cache_root: Path) -> Path:
    if is_extracted_gx3_root(cache_root):
        return cache_root
    roots = find_extracted_gx3_roots(cache_root)
    if roots:
        return roots[0]
    return cache_root


def extract_gx3_archive(path: Path) -> Path:
    """Extract a .gx3 ZIP archive into .gx3_cache/<sha256>/ and return its root."""

    source = path.expanduser().resolve()
    if not source.exists():
        raise ProjectRootError(f"project archive not found: {source}")
    if not source.is_file():
        raise ProjectRootError(f"project archive is not a file: {source}")
    try:
        digest = gx3_archive_hash(source)
    except OSError as exc:
        raise ProjectRootError(f"cannot read project archive {source}: {exc}") from exc

    cache_root = _cache_root_for(source, digest)
    marker = cache_root / ".gx3_cache_complete"
    if marker.exists():
        return _project_root_inside_cache(cache_root)

    tmp_root = cache_root.with_name(f"{cache_root.name}.tmp-{os.getpid()}")
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    try:
        tmp_root.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(source) as zf:
            bad_member = zf.testzip()
            if bad_member:
                raise ProjectRootError(f"corrupt member in .gx3 archive: {bad_member}")
            zf.extractall(tmp_root)
        (tmp_root / ".gx3_cache_complete").write_text("", encoding="utf-8")
        if cache_root.exists():
            shutil.rmtree(cache_root)
        tmp_root.replace(cache_root)
    except zipfile.BadZipFile as exc:
        if tmp_root.exists():
            shutil.rmtree(tmp_root)
        raise ProjectRootError(
            f"cannot open .gx3 archive {source}; the file is not a valid ZIP/GX3 container"
        ) from exc
    except (OSError, ProjectRootError):
        if tmp_root.exists():
            shutil.rmtree(tmp_root)
        raise
    return _project_root_inside_cache(cache_root)


def resolve_project_root(path: str | Path) -> Path:
    """Resolve a folder or .gx3 file to an extracted project directory."""

    root = Path(path).expanduser()
    if root.suffix.lower() == ".gx3":
        return extract_gx3_archive(root)
    return root


# Where a failing run should send the user. A message that says "make a support
# bundle" and then stops leaves them with nowhere to put it, which is how a
# parser gap stays invisible: the tool fails and the user closes the window.
REPORT_URL = "https://github.com/purinzan/gx3-cli-mcp/issues/new?template=parser-gap.yml"


def project_root_error_message(reason: str) -> str:
    return "\n".join(
        [
            "ERROR: could not prepare the GX3 project input.",
            f"Reason: {reason}",
            "",
            "Next steps:",
            "  1. Confirm the input is a supported GX Works3 .gx3 file or an extracted",
            "     project folder. A .gx3 saved with the compressed/lightweight option is",
            "     password protected and cannot be read by this tool.",
            "  2. For parser coverage issues, run:",
            "       gx3-cli parse-gaps --root <project>",
            "  3. Build a redacted bundle (no ladder body, no device comments):",
            "       gx3-cli support-bundle --root <project>",
            "  4. Report it, and attach that bundle:",
            f"       {REPORT_URL}",
            "",
            "Reports are what fix parser gaps. Do not paste project data, device",
            "comments, equipment names or addresses -- the bundle is redacted for",
            "exactly that reason.",
        ]
    )


IGNORED_ROOT_PREFIXES = ("_work_", "_verify_", "_ARCHIVE", "_KEEP")


def find_extracted_gx3_roots(base: Path | None = None) -> list[Path]:
    base = (base or Path.cwd()).resolve()
    if not base.is_dir():
        return []
    roots: list[Path] = []
    if is_extracted_gx3_root(base):
        roots.append(base)
    for child in base.iterdir():
        if not child.is_dir() or child.name.startswith(IGNORED_ROOT_PREFIXES):
            continue
        if is_extracted_gx3_root(child):
            roots.append(child)
    # prefer proper _extracted_* folders over other matches, newest first
    return sorted(
        set(roots),
        key=lambda p: (p.name.startswith("_extracted_"), p.stat().st_mtime, str(p)),
        reverse=True,
    )


def root_from_index_dbs(base: Path | None = None) -> Path | None:
    """Recover the last-indexed project root from `.gx3_index/*.sqlite` meta.

    Lets tools resolve --root from any working directory (e.g. when run from the
    tool folder while the extracted project lives one level up), instead of
    silently falling back to `.` and returning empty/false results.
    """
    base = (base or Path.cwd()).resolve()
    index_dir = base / ".gx3_index"
    if not index_dir.is_dir():
        return None
    dbs = sorted(index_dir.glob("*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    for db in dbs:
        try:
            con = sqlite3.connect(str(db))
            row = con.execute("select value from meta where key='root'").fetchone()
            con.close()
        except sqlite3.Error:
            continue
        if row and row[0]:
            candidate = Path(str(row[0]))
            if candidate.exists() or (base / candidate).exists():
                return candidate
    return None


def default_project_root(base: Path | None = None) -> Path:
    env_root = first_env(ROOT_ENV, LEGACY_ROOT_ENV)
    if env_root:
        return resolve_project_root(env_root)
    roots = find_extracted_gx3_roots(base)
    if roots:
        return roots[0]
    index_root = root_from_index_dbs(base)
    if index_root:
        return index_root
    return Path(".")


def find_comment_db(root: Path) -> Path | None:
    candidates = sorted(root.glob("*_DC.db"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return candidates[0] if candidates else None


def default_output_prefix(suffix: str | None = None) -> str:
    base = first_env(OUTPUT_PREFIX_ENV, LEGACY_OUTPUT_PREFIX_ENV) or DEFAULT_OUTPUT_PREFIX
    return f"{base}_{suffix}" if suffix else base


def default_output_path(suffix: str, extension: str) -> Path:
    return Path(f"{default_output_prefix(suffix)}.{extension.lstrip('.')}")


def default_comm_prefix() -> str:
    return first_env(COMM_PREFIX_ENV, LEGACY_COMM_PREFIX_ENV) or default_output_prefix("comm")
