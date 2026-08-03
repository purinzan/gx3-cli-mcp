from __future__ import annotations

import os
import hashlib
import shutil
import sqlite3
import zipfile
from pathlib import Path


DEFAULT_OUTPUT_PREFIX = "project"
ROOT_ENV = "PROJECT_ROOT"
LEGACY_ROOT_ENV = "GX3_ROOT"
OUTPUT_PREFIX_ENV = "PROJECT_OUTPUT_PREFIX"
LEGACY_OUTPUT_PREFIX_ENV = "GX3_OUTPUT_PREFIX"
COMM_PREFIX_ENV = "PROJECT_COMM_PREFIX"
LEGACY_COMM_PREFIX_ENV = "GX3_COMM_PREFIX"


class ProjectRootError(ValueError):
    """Raised when a project root or .gx3 archive cannot be prepared."""


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
        or any(path.glob("*_DC.db"))
    )


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


def project_root_error_message(reason: str) -> str:
    return "\n".join(
        [
            "ERROR: could not prepare the GX3 project input.",
            f"Reason: {reason}",
            "",
            "Next steps:",
            "  - Confirm the input is a supported GX Works3 .gx3 file or an extracted project folder.",
            "  - For parser coverage issues, run: gx3-cli parse-gaps --root <project>",
            "  - For support, create a redacted bundle: gx3-cli support-bundle --root <project>",
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
