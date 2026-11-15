"""Finds databases on this computer that may hold business data, and describes their shape.

Only names and counts leave this module: table names, column names and types, row counts. Row values are read only when
the caller asks for `samples`, and then a few short values per column. Every file is opened read-only, so nothing is
ever written to a system's database, and the system keeps running while it is looked at. Standard library only."""
import os
import sqlite3
from pathlib import Path

SQLITE_HEADER = b"SQLite format 3\x00"
EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".db3", ".sdb"}
SKIP_PARTS = {                                  # browsers, the operating system, developer tools: databases that are not business data
    "google", "chrome", "chromium", "mozilla", "firefox", "edge", "brave", "opera", "microsoft", "windows", "program files",
    "program files (x86)", "programdata", "$recycle.bin", "system volume information", "node_modules", ".git", ".cache", "cache",
    "caches", "packages", "temp", "tmp",
}
MAX_ROWS_COUNTED = 1_000_000


def find_databases(roots, max_depth: int = 5, max_files: int = 300, min_bytes: int = 16 * 1024) -> list[Path]:
    """SQLite files under `roots`, newest first. A file counts only if its first bytes say it is SQLite, so a renamed
    document or an encrypted database is not mistaken for one."""
    found: list[Path] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            here = Path(dirpath)
            if len(here.parts) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_PARTS]     # judged by the folder's own name, so where the search starts is never skipped
            for name in filenames:
                p = here / name
                if p.suffix.lower() not in EXTENSIONS:
                    continue
                try:
                    if p.stat().st_size < min_bytes:
                        continue
                    with open(p, "rb") as f:
                        if f.read(16) != SQLITE_HEADER:
                            continue
                except OSError:
                    continue
                found.append(p)
                if len(found) >= max_files:
                    return _newest_first(found)
    return _newest_first(found)


def _newest_first(paths: list[Path]) -> list[Path]:
    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    return sorted(set(paths), key=mtime, reverse=True)


def describe(path, samples: int = 0, sample_chars: int = 30) -> dict:
    """What one database holds: tables with their columns and row counts. `error` is set, with nothing else, when it
    cannot be read (locked, damaged, or encrypted)."""
    path = Path(path)
    out = {"path": str(path), "size": None, "modified": None, "tables": [], "error": None}
    try:
        st = path.stat()
        out["size"], out["modified"] = st.st_size, int(st.st_mtime)
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)
    except (OSError, sqlite3.Error) as e:
        out["error"] = f"cannot be opened: {e}"
        return out
    try:
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name"):
            quoted = '"' + name.replace('"', '""') + '"'
            cols = [{"name": c[1], "type": (c[2] or "").upper(), "pk": bool(c[5])} for c in con.execute(f"PRAGMA table_info({quoted})")]
            rows = con.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {quoted} LIMIT {MAX_ROWS_COUNTED + 1})").fetchone()[0]
            table = {"name": name, "rows": min(rows, MAX_ROWS_COUNTED), "more_rows": rows > MAX_ROWS_COUNTED, "columns": cols}
            if samples and rows:
                for c in cols:
                    q = '"' + c["name"].replace('"', '""') + '"'
                    vals = [str(r[0])[:sample_chars] for r in con.execute(f"SELECT DISTINCT {q} FROM {quoted} WHERE {q} IS NOT NULL LIMIT {int(samples)}")]
                    c["samples"] = vals
            out["tables"].append(table)
    except sqlite3.Error as e:
        out.update(tables=[], error=f"cannot be read: {e}")
    finally:
        con.close()
    return out
