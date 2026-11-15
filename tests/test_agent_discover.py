"""Finding databases on a computer and describing their shape, without ever writing to them."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import discover as D  # noqa: E402


def make_db(path: Path, rows: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sold_at TEXT, total REAL, customer TEXT)")
    con.executemany("INSERT INTO sales (sold_at, total, customer) VALUES (?, ?, ?)", [(f"2026-01-0{i + 1}", 100.0 * (i + 1), f"c{i}") for i in range(rows)])
    con.execute("CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO staff (name) VALUES (?)", [("a" * 40,)] * 2000)        # padding so the file passes the size floor
    con.commit()
    con.close()
    return path


def test_finds_business_databases_and_skips_browsers_and_look_alikes(tmp_path):
    shop = make_db(tmp_path / "Shop" / "data" / "pos.db")
    make_db(tmp_path / "Google" / "Chrome" / "History.db")                          # a browser's own database
    make_db(tmp_path / "node_modules" / "x" / "cache.sqlite")
    (tmp_path / "Shop" / "notes.db").write_bytes(b"this is not sqlite" * 2000)       # renamed document
    (tmp_path / "Shop" / "tiny.db").write_bytes(D.SQLITE_HEADER + b"\0" * 100)       # under the size floor
    (tmp_path / "Shop" / "readme.txt").write_text("x")
    assert D.find_databases([tmp_path]) == [shop]


def test_limits_depth_and_count_and_ignores_missing_roots(tmp_path):
    make_db(tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "deep.db")
    near = make_db(tmp_path / "near.db")
    assert D.find_databases([tmp_path, tmp_path / "nope"]) == [near]                # six folders down is beyond the default reach
    for i in range(4):
        make_db(tmp_path / f"m{i}.db")
    assert len(D.find_databases([tmp_path], max_files=3)) == 3


def test_newest_first(tmp_path):
    old, new = make_db(tmp_path / "old.db"), make_db(tmp_path / "new.db")
    os.utime(old, (1_000_000, 1_000_000))
    assert D.find_databases([tmp_path]) == [new, old]


def test_describe_names_tables_columns_and_counts_but_no_values_by_default(tmp_path):
    d = D.describe(make_db(tmp_path / "pos.db"))
    assert d["error"] is None and [t["name"] for t in d["tables"]] == ["sales", "staff"]
    sales = d["tables"][0]
    assert sales["rows"] == 3 and sales["more_rows"] is False
    assert [(c["name"], c["type"], c["pk"]) for c in sales["columns"]] == [("id", "INTEGER", True), ("sold_at", "TEXT", False), ("total", "REAL", False), ("customer", "TEXT", False)]
    assert all("samples" not in c for t in d["tables"] for c in t["columns"])


def test_describe_gives_a_few_short_values_only_when_asked(tmp_path):
    d = D.describe(make_db(tmp_path / "pos.db"), samples=2, sample_chars=5)
    cols = {c["name"]: c for c in d["tables"][0]["columns"]}
    assert cols["sold_at"]["samples"] == ["2026-", "2026-"][:len(cols["sold_at"]["samples"])] and len(cols["sold_at"]["samples"]) == 2


def test_describe_never_writes_and_leaves_the_file_untouched(tmp_path):
    p = make_db(tmp_path / "pos.db")
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    D.describe(p, samples=3)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before
    assert not list(tmp_path.glob("pos.db-*"))                                       # no journal or wal file was left behind


def test_an_unreadable_database_is_reported_not_raised(tmp_path):
    bad = tmp_path / "enc.db"
    bad.write_bytes(os.urandom(40_000))
    d = D.describe(bad)
    assert d["tables"] == [] and d["error"]
    assert D.describe(tmp_path / "missing.db")["error"]


def test_odd_names_are_quoted_not_executed(tmp_path):
    p = tmp_path / "odd.db"
    con = sqlite3.connect(p)
    con.execute('CREATE TABLE "we""ird; DROP TABLE x" ("a b" TEXT)')
    con.executemany('INSERT INTO "we""ird; DROP TABLE x" VALUES (?)', [("v" * 50,)] * 400)
    con.commit(); con.close()
    t = D.describe(p, samples=1)["tables"][0]
    assert t["name"] == 'we"ird; DROP TABLE x' and t["rows"] == 400 and t["columns"][0]["samples"] == ["v" * 30]
