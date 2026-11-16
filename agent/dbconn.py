"""Databases other than a file on this computer: PostgreSQL, MySQL/MariaDB, SQL Server, and anything with an ODBC driver (Microsoft
Access among them), on this computer, on the local network, or hosted.

A connection's details (host, user, password) live in a file on THIS computer, readable only by the person who installed the agent.
They are never sent to the app; the app only ever sees table and column names. The agent still reads nothing it is not asked to
and sends nothing back to the database: sessions are opened read-only where the database allows it, every statement must be a
SELECT, and `writable()` tells the owner when the login is allowed to change data so they can give the agent a read-only one.

The drivers are pure Python (pg8000, pymysql, python-tds) except ODBC (pyodbc, which also needs the database's own ODBC driver).
Each is loaded only when a connection of that kind is used, so a computer that only has file databases needs none of them."""
import json
import os
import re
from pathlib import Path

KINDS = ("postgres", "mysql", "mssql", "odbc")
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "mssql": 1433}
MAX_ROWS_COUNTED = 1_000_000
_LOCAL = re.compile(r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|::1$|[^.]+$)")     # this computer, a private network, or a bare machine name


class DbError(Exception):
    """A statement failed (a driver's own error, made uniform so the agent handles every database the same way)."""


class DbProblem(Exception):
    """Something the owner can act on, in plain words. `kind` is one of: driver, unreachable, login, missing, unreadable."""
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


# ---- the store: connections.json beside the agent's other files -------------------------------------------------------

def load_connections(home: Path) -> dict[str, dict]:
    try:
        data = json.loads((home / "connections.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("kind") in KINDS} if isinstance(data, dict) else {}


def save_connections(home: Path, conns: dict[str, dict]) -> None:
    path = home / "connections.json"
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(conns, f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def public(spec: dict) -> dict:
    """A connection without its secrets, for showing."""
    return {k: v for k, v in spec.items() if k not in ("password", "connection_string")}


# ---- dialects ---------------------------------------------------------------------------------------------------------

class Dialect:
    def __init__(self, name, open_q, close_q, text_type, top):
        self.name, self.open_q, self.close_q, self.text_type, self.top = name, open_q, close_q, text_type, top      # top: "TOP n" before the columns, else "LIMIT n" after

    def quote(self, ident: str) -> str:
        return self.open_q + str(ident).replace(self.close_q, self.close_q * 2) + self.close_q

    def table(self, schema, name) -> str:
        return f"{self.quote(schema)}.{self.quote(name)}" if schema else self.quote(name)

    def text(self, expr: str) -> str:
        return f"CAST({expr} AS {self.text_type})"

    def limited(self, columns: str, source: str, n: int, where: str = "") -> str:
        w = f" WHERE {where}" if where else ""
        return f"SELECT TOP {n} {columns} FROM {source}{w}" if self.top else f"SELECT {columns} FROM {source}{w} LIMIT {n}"


SQLITE = Dialect("sqlite", '"', '"', "TEXT", False)
DIALECTS = {
    "sqlite": SQLITE,
    "postgres": Dialect("postgres", '"', '"', "TEXT", False),
    "mysql": Dialect("mysql", "`", "`", "CHAR", False),
    "mssql": Dialect("mssql", "[", "]", "NVARCHAR(200)", True),
    "access": Dialect("access", "[", "]", "TEXT(255)", True),
    "ansi": Dialect("ansi", '"', '"', "VARCHAR(200)", False),
}


def dialect_for(spec: dict) -> Dialect:
    if spec["kind"] == "odbc":
        return DIALECTS["access" if "access" in str(spec.get("connection_string", "")).lower() else "ansi"]
    return DIALECTS[spec["kind"]]


def convert_placeholders(sql: str, style: str) -> str:
    """The agent writes `?`; drivers that want `%s` get it, with literal percent signs protected. Quoted text is left alone."""
    if style == "qmark":
        return sql
    out, quote = [], None
    for ch in sql:
        if quote:
            quote = None if ch == quote else quote
            out.append("%%" if ch == "%" else ch)
        elif ch in "'\"`":
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append("%%" if ch == "%" else ch)
    return "".join(out)


# ---- connecting -------------------------------------------------------------------------------------------------------

def _need(module: str, pip: str, why: str):
    try:
        return __import__(module, fromlist=["x"])
    except ImportError:
        raise DbProblem("driver", f"{why} needs a small reader installed once on this computer: pip install {pip}")


def _classify(spec: dict, e: Exception) -> DbProblem:
    msg = str(e)
    low = msg.lower()
    host = spec.get("host") or "the server"
    if "to database" in low or any(w in low for w in ("does not exist", "unknown database", "cannot open database", "1049", "3d000")):
        return DbProblem("missing", f"The server answered, but the database '{spec.get('database', '')}' does not exist, or this user is not allowed to use it. Check the name and the user's rights.")
    if any(w in low for w in ("password", "authentication", "login failed", "access denied", "role", "28p01", "1045")):
        return DbProblem("login", "The database refused the user name or password. Check them, and that this user may connect from this computer.")
    if any(w in low for w in ("timed out", "timeout", "refused", "unreachable", "name or service", "getaddrinfo", "no route", "could not connect", "can't connect", "network", "reset")):
        return DbProblem("unreachable", f"Could not reach {host}. Is it switched on, is the address and port right, and does its firewall allow this computer?")
    return DbProblem("unreadable", f"The database could not be opened ({type(e).__name__}: {msg[:160]}).")


class Conn:
    """A read-only connection to a server database, shaped like the sqlite3 connection the agent already works with."""

    def __init__(self, raw, spec: dict, style: str):
        self.raw, self.spec, self.style, self.dialect = raw, spec, style, dialect_for(spec)

    def execute(self, sql: str, params=()):
        try:
            cur = self.raw.cursor()
            cur.execute(convert_placeholders(sql, self.style), tuple(params.values()) if isinstance(params, dict) else tuple(params))
            return cur
        except Exception as e:
            raise DbError(f"{type(e).__name__}: {e}") from e

    def begin(self) -> None:
        """One snapshot for a whole pass, where the database offers one: what is read is then consistent even while the system is writing."""
        stmt = {"postgres": "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY", "mysql": "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"}.get(self.spec["kind"])
        if stmt:
            self.execute(stmt)

    def end(self) -> None:
        if self.spec["kind"] in ("postgres", "mysql"):
            self.execute("ROLLBACK")

    def table_names(self) -> set[str]:
        if self.spec["kind"] == "odbc":
            return {t.table_name for t in self.raw.cursor().tables(tableType="TABLE")}
        sql, has_schema = _TABLES[self.spec["kind"]]
        names: set[str] = set()
        for r in self.execute(sql).fetchall():
            names.add(f"{r[0]}.{r[1]}" if has_schema else r[1])
            names.add(r[1])
        return names

    def close(self) -> None:
        try:
            self.raw.close()
        except Exception:
            pass

    def compile_check(self, sql: str, params=()) -> None:
        """Raises if the statement does not compile against the real schema, without running it."""
        kind = self.spec["kind"]
        if kind == "mssql":
            self.execute("EXEC sp_describe_first_result_set @tsql = ?", (sql,)).fetchall()
        elif kind in ("postgres", "mysql"):
            self.execute("EXPLAIN " + sql, params).fetchall()
        else:
            cur = self.execute(f"SELECT * FROM ({sql}) AS probe WHERE 1 = 0") if self.dialect.name == "ansi" else self.execute(sql)
            cur.fetchmany(1)

    def writable(self) -> bool | None:
        """True when this login is allowed to change data (so the owner should give the agent a read-only one); None when unknown."""
        kind = self.spec["kind"]
        try:
            if kind == "postgres":
                return bool(self.execute("SELECT has_database_privilege(current_user, current_database(), 'CREATE') OR EXISTS (SELECT 1 FROM information_schema.role_table_grants WHERE grantee = current_user AND privilege_type IN ('INSERT','UPDATE','DELETE')) OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)").fetchone()[0])
            if kind == "mysql":
                grants = " ".join(str(r[0]) for r in self.execute("SHOW GRANTS").fetchall()).upper()
                return bool(re.search(r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", grants))
            if kind == "mssql":
                return bool(self.execute("SELECT CASE WHEN IS_MEMBER('db_owner') = 1 OR IS_MEMBER('db_datawriter') = 1 OR IS_SRVROLEMEMBER('sysadmin') = 1 THEN 1 ELSE 0 END").fetchone()[0])
        except Exception:
            return None
        return None

    def describe(self, samples: int = 0, sample_chars: int = 30) -> dict:
        return describe_server(self, samples, sample_chars)


def connect(spec: dict, timeout: int = 10, readonly: bool = True) -> Conn:
    kind, host = spec["kind"], spec.get("host") or "localhost"
    ssl = spec.get("ssl")
    if ssl is None:
        ssl = not _LOCAL.match(host)                                  # off the local network: encrypted unless told otherwise
    try:
        if kind == "postgres":
            pg = _need("pg8000.dbapi", "pg8000", "PostgreSQL")
            raw = pg.connect(user=spec.get("user"), password=spec.get("password"), host=host, port=int(spec.get("port") or 5432), database=spec.get("database"),
                             timeout=timeout, ssl_context=True if ssl else None)
            raw.autocommit = True
            c = Conn(raw, spec, "format")
            if readonly:
                c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            c.execute("SET statement_timeout = 300000")
        elif kind == "mysql":
            my = _need("pymysql", "pymysql", "MySQL")
            raw = my.connect(user=spec.get("user"), password=spec.get("password"), host=host, port=int(spec.get("port") or 3306), database=spec.get("database"),
                             connect_timeout=timeout, read_timeout=300, ssl={} if ssl else None, autocommit=True)
            c = Conn(raw, spec, "format")
            if readonly:
                c.execute("SET SESSION TRANSACTION READ ONLY")
        elif kind == "mssql":
            tds = _need("pytds", "python-tds", "SQL Server")
            raw = tds.connect(server=host, port=int(spec.get("port") or 1433), database=spec.get("database"), user=spec.get("user"), password=spec.get("password"),
                              login_timeout=timeout, timeout=300, autocommit=True, readonly=readonly, cafile=None if not ssl else _cafile(), validate_host=False)
            c = Conn(raw, spec, "format")
        elif kind == "odbc":
            odbc = _need("pyodbc", "pyodbc", "This database")
            raw = odbc.connect(spec["connection_string"], timeout=timeout, readonly=readonly, autocommit=True)
            c = Conn(raw, spec, "qmark")
        else:
            raise DbProblem("unreadable", f"Unknown database type '{kind}'.")
    except DbProblem:
        raise
    except Exception as e:
        raise _classify(spec, e)
    return c


def _cafile():
    try:
        import certifi
        return certifi.where()
    except ImportError:
        return None


# ---- describing: names, types, counts; row values only when asked -----------------------------------------------------

_TABLES = {
    "postgres": ("SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2", True),
    "mysql": ("SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema = DATABASE() ORDER BY 2", False),
    "mssql": ("SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' ORDER BY 1, 2", True),
}
_COLUMNS = {
    "postgres": "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
    "mysql": "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ? ORDER BY ordinal_position",
    "mssql": "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
}
_KEYS = {
    "postgres": "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) WHERE i.indisprimary AND i.indrelid = (quote_ident(?) || '.' || quote_ident(?))::regclass",    # information_schema hides keys from a read-only login
    "mysql": "SELECT column_name FROM information_schema.key_column_usage WHERE constraint_name = 'PRIMARY' AND table_schema = DATABASE() AND table_name = ?",
    "mssql": "SELECT kcu.column_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = ? AND tc.table_name = ?",
}
_ESTIMATES = {                                      # the catalog's own row counts: instant, and no table is scanned
    "postgres": "SELECT n.nspname, c.relname, c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r'",
    "mysql": "SELECT table_schema, table_name, table_rows FROM information_schema.tables WHERE table_schema = DATABASE()",
    "mssql": "SELECT s.name, t.name, SUM(p.rows) FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) GROUP BY s.name, t.name",
}


def _table_entry(schema, name, rows, columns):
    return {"name": f"{schema}.{name}" if schema else name, "schema": schema, "table": name, "rows": rows, "more_rows": bool(rows and rows >= MAX_ROWS_COUNTED), "columns": columns}


def describe_server(c: Conn, samples: int = 0, sample_chars: int = 30) -> dict:
    spec = c.spec
    label = spec.get("name") or spec.get("database") or spec.get("host") or spec["kind"]
    out = {"path": f"{spec['kind']}://{spec.get('host') or 'local'}/{spec.get('database') or label}", "size": None, "modified": None, "tables": [], "error": None,
           "source": {"kind": spec["kind"], "name": spec.get("name")}}
    try:
        if spec["kind"] == "odbc":
            out["tables"] = _describe_odbc(c, samples, sample_chars)
            return out
        sql, has_schema = _TABLES[spec["kind"]]
        pairs = [(r[0] if has_schema else None, r[1]) for r in c.execute(sql).fetchall()]
        est: dict = {}
        try:
            est = {(r[0], r[1]): int(r[2] or 0) for r in c.execute(_ESTIMATES[spec["kind"]]).fetchall()}
        except Exception:
            pass
        for schema, name in pairs:
            args = (schema, name) if has_schema else (name,)
            cols = [{"name": r[0], "type": str(r[1] or "").upper(), "pk": False} for r in c.execute(_COLUMNS[spec["kind"]], args).fetchall()]
            keys = {r[0] for r in c.execute(_KEYS[spec["kind"]], args).fetchall()}
            for col in cols:
                col["pk"] = col["name"] in keys
            rows = est.get((schema, name), est.get((name, name)))
            if rows is None or rows < 0:
                rows = _count(c, schema, name)
            entry = _table_entry(schema, name, min(rows, MAX_ROWS_COUNTED), cols)
            if samples and rows:
                _sample(c, schema, name, cols, samples, sample_chars)
            out["tables"].append(entry)
    except DbProblem:
        raise
    except Exception as e:
        out.update(tables=[], error=_classify(spec, e).message)
    return out


def _count(c: Conn, schema, name) -> int:
    try:
        inner = c.dialect.limited("1", c.dialect.table(schema, name), MAX_ROWS_COUNTED + 1)
        return int(c.execute(f"SELECT COUNT(*) FROM ({inner}) AS t").fetchone()[0])
    except Exception:
        return 0


def _sample(c: Conn, schema, name, cols, samples, chars) -> None:
    d = c.dialect
    for col in cols:
        q = d.quote(col["name"])
        try:
            col["samples"] = [str(r[0])[:chars] for r in c.execute(d.limited(f"DISTINCT {q}", d.table(schema, name), int(samples), f"{q} IS NOT NULL")).fetchall()]
        except Exception:
            col["samples"] = []


def _describe_odbc(c: Conn, samples, chars) -> list[dict]:
    cur = c.raw.cursor()
    tables = []
    for t in cur.tables(tableType="TABLE"):
        name = t.table_name
        if name.startswith("MSys") or name.startswith("~"):
            continue
        cols = [{"name": r.column_name, "type": str(r.type_name or "").upper(), "pk": False} for r in c.raw.cursor().columns(table=name)]
        keys = {r.column_name for r in c.raw.cursor().primaryKeys(table=name)}
        for col in cols:
            col["pk"] = col["name"] in keys
        rows = _count(c, None, name)
        tables.append(_table_entry(None, name, min(rows, MAX_ROWS_COUNTED), cols))
        if samples and rows:
            _sample(c, None, name, cols, samples, chars)
    return tables


# ---- finding servers and the databases on them, so nobody has to type a name ----------------------------------------

_SYSTEM_DATABASES = {
    "mysql": {"information_schema", "mysql", "performance_schema", "sys"},
    "postgres": {"template0", "template1"},
    "mssql": {"master", "tempdb", "model", "msdb"},
}


def _open_port(host: str, port: int, wait: float) -> bool:
    import socket
    try:
        with socket.create_connection((host, port), timeout=wait):
            return True
    except OSError:
        return False


def own_network_hosts() -> list[str]:
    """The other addresses on this computer's own /24 (a small office network). Nothing outside it is ever probed."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))                     # no packet is sent; this only picks the local address
            mine = s.getsockname()[0]
    except OSError:
        return []
    base = mine.rsplit(".", 1)[0]
    return [f"{base}.{i}" for i in range(1, 255) if f"{base}.{i}" != mine]


def find_servers(hosts=("127.0.0.1",), wait: float = 0.4) -> list[dict]:
    """Database servers answering on their usual ports at these addresses: [{kind, host, port}]. Ports only; no login is tried."""
    from concurrent.futures import ThreadPoolExecutor
    probes = [(kind, host, port) for host in hosts for kind, port in DEFAULT_PORTS.items()]
    with ThreadPoolExecutor(max_workers=64) as pool:
        up = list(pool.map(lambda p: _open_port(p[1], p[2], wait), probes))
    found = [{"kind": k, "host": h, "port": p} for (k, h, p), ok in zip(probes, up) if ok]
    for dsn in odbc_sources():
        found.append(dsn)
    return found


def odbc_sources() -> list[dict]:
    """Databases already set up on this computer as ODBC data sources (Access files and others), if pyodbc is installed."""
    try:
        import pyodbc
        return [{"kind": "odbc", "host": None, "port": None, "name": name, "connection_string": f"DSN={name}"} for name in pyodbc.dataSources()]
    except Exception:
        return []


def list_databases(spec: dict, timeout: int = 8) -> list[dict]:
    """The databases a login can see on a server, system ones left out: [{name, tables}] (tables is None when it is not cheap to know)."""
    kind = spec["kind"]
    probe = {**spec, "database": spec.get("database") or {"mysql": None, "postgres": "postgres", "mssql": "master"}[kind]}
    if probe["database"] is None:
        probe.pop("database")
    c = connect(probe, timeout=timeout)
    try:
        if kind == "mysql":
            tables = {r[0]: int(r[1]) for r in c.execute("SELECT table_schema, COUNT(*) FROM information_schema.tables WHERE table_type = 'BASE TABLE' GROUP BY table_schema").fetchall()}
            names = [r[0] for r in c.execute("SHOW DATABASES").fetchall()]
        elif kind == "postgres":
            names = [r[0] for r in c.execute("SELECT datname FROM pg_database WHERE NOT datistemplate AND datallowconn ORDER BY 1").fetchall()]
            tables = {}
        else:
            names = [r[0] for r in c.execute("SELECT name FROM sys.databases WHERE HAS_DBACCESS(name) = 1 ORDER BY 1").fetchall()]
            tables = {}
        return [{"name": n, "tables": tables.get(n)} for n in names if n not in _SYSTEM_DATABASES.get(kind, set())]
    except Exception as e:
        raise _classify(spec, e)
    finally:
        c.close()
