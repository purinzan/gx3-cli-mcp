from __future__ import annotations

"""Release gate: prove a built artifact carries no data and no confidential residue.

Run before publishing a source tree, and after ``python -m build`` for built
artifacts:

    python scripts/release_gate.py .
    python scripts/release_gate.py dist/gx3_cli_mcp-*.whl

Checks:
  1. no source-tree or archive member is a confidential project artifact
  2. no text source contains customer project codenames, IP addresses, or
     developer machine paths

Exit code is non-zero on any violation so this can gate CI / a publish script.
"""

import glob
import re
import os
import sys
import tarfile
import zipfile
from pathlib import Path


TEXT_SUFFIXES = {".py", ".md", ".toml", ".txt", ".json", ".in", ".yml", ".yaml"}
TEXT_NAMES = {".gitattributes", ".gitignore"}
FORBIDDEN_SUFFIXES = {
    ".gx3", ".gtx", ".db", ".sqlite", ".sqlite3", ".zip", ".xlsx", ".xls",
    ".pdf", ".cab", ".dat", ".xml", ".bin", ".w3pa", ".iut", ".gpj", ".prj",
    ".pji", ".col", ".qpg", ".pcode", ".info", ".csv", ".jwt", ".jwk", ".pem", ".key",
}
ALLOWED_METADATA_MARKERS = ("dist-info/", ".egg-info/", "PKG-INFO", "MANIFEST.in", "pyproject.toml", "setup.cfg")
SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", "build", "dist", "dist_sdist"}

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
USER_HOME_PATTERN = r"C:" + r"\\Users\\[^\\]+"
CLOUD_SYNC_PATTERN = "One" + "Drive"
USERPATH_RE = re.compile(USER_HOME_PATTERN + "|" + CLOUD_SYNC_PATTERN, re.IGNORECASE)


def forbidden_terms() -> list[str]:
    """Load site-specific forbidden company/equipment terms without embedding
    those names in the distributable source tree."""
    terms: list[str] = []
    raw = os.environ.get("GX3_RELEASE_FORBIDDEN_TERMS", "")
    terms.extend(term.strip() for term in raw.split(",") if term.strip())
    path = os.environ.get("GX3_RELEASE_FORBIDDEN_TERMS_FILE", "")
    if path:
        terms.extend(
            line.strip()
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    return terms

def archive_members(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            return [(n, z.read(n)) for n in z.namelist() if not n.endswith("/")]
    if path.name.endswith(".tar.gz") or path.suffix == ".tgz":
        with tarfile.open(path) as t:
            out = []
            for m in t.getmembers():
                if m.isfile():
                    f = t.extractfile(m)
                    out.append((m.name, f.read() if f else b""))
            return out
    raise SystemExit(f"unsupported artifact type: {path}")


def source_members(path: Path) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in child.parts):
            continue
        rel = child.relative_to(path).as_posix()
        out.append((rel, child.read_bytes()))
    return out


def members(path: Path) -> list[tuple[str, bytes]]:
    if path.is_dir():
        return source_members(path)
    return archive_members(path)


def is_metadata(name: str) -> bool:
    return any(marker in name for marker in ALLOWED_METADATA_MARKERS)


def expand_artifact_args(argv: list[str]) -> list[str]:
    """Expand shell wildcards when the caller's shell does not do it.

    PowerShell passes wildcards to Python unchanged, while bash expands them
    before process launch. Expanding here keeps the documented command portable.
    """
    expanded: list[str] = []
    for arg in argv:
        if Path(arg).exists() or not glob.has_magic(arg):
            expanded.append(arg)
            continue
        matches = sorted(glob.glob(arg))
        expanded.extend(matches or [arg])
    return expanded


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit("usage: release_gate.py <artifact.whl|artifact.tar.gz> ...")
    violations: list[str] = []
    for arg in expand_artifact_args(argv):
        path = Path(arg)
        if not path.exists():
            violations.append(f"artifact not found: {path}")
            continue
        print(f"== {path.name}")
        for name, data in members(path):
            suffix = Path(name).suffix.lower()
            if suffix in FORBIDDEN_SUFFIXES:
                violations.append(f"{path.name}: forbidden project/data artifact: {name}")
                continue
            if suffix not in TEXT_SUFFIXES and Path(name).name not in TEXT_NAMES and not is_metadata(name):
                continue
            text = data.decode("utf-8", errors="replace")
            extra_terms = forbidden_terms()
            for lineno, line in enumerate(text.splitlines(), 1):
                for pattern, label in ((IP_RE, "ip"), (USERPATH_RE, "userpath")):
                    m = pattern.search(line)
                    if m:
                        violations.append(f"{path.name}:{name}:{lineno} [{label}] {m.group(0)!r} -> {line.strip()[:90]}")
                for term in extra_terms:
                    if term.lower() in line.lower():
                        violations.append(f"{path.name}:{name}:{lineno} [forbidden-term] {term!r} -> {line.strip()[:90]}")

    if violations:
        print("\nRELEASE GATE FAILED:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("\nRELEASE GATE PASSED: artifact is code-only with no confidential residue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
