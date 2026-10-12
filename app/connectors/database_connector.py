"""
Database connector (Section 8-C): read-only connections to a business's
existing database (Postgres/MySQL/etc).

Two things this must never do (Section 9 — the security principle that
applies to every connector, restated here because a DB connector is the
one most tempting to violate it with):
- Never write to the business's database. Read-only, always.
- Never run arbitrary caller-supplied SQL against it. The query is fixed
  at connector configuration time by whoever sets up the connector, not
  accepted per-request — otherwise this becomes a general SQL execution
  endpoint against a business's production database, which is a very
  different (and unacceptable) risk profile.

Verified against a real server in tests: PostgreSQL and SQLite. MySQL, SQL Server, Oracle and generic ODBC are wired
with the right drivers, URL building, read-only settings and error handling (unit-tested), but have NOT been run
against a live server here - treat them as unverified until tried (see DRIVERS[...].verified and GET /api/connectors/database-types).
"""
import re
from typing import Optional
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL
from pydantic import BaseModel
from fastapi import HTTPException

from app.core.netguard import check_host_allowed, tenant_path
from app.core.secrets import decrypt_config_secrets
from app.connectors.file_connector import to_number

# Fixed allowlist - a connector's db_type must be one of these, never an arbitrary driver string from user input.
# `verified` says whether this path has been exercised against a real server in this project's tests; the rest are
# wired up with the right driver and read-only settings but MUST be tried against a real server before being relied on.
class DriverSpec(BaseModel):
    dialect: Optional[str]          # SQLAlchemy driver name; None for raw-driver paths (odbc)
    module: str                     # import name that must be installable
    default_port: Optional[int]
    verified: bool
    notes: str = ""


DRIVERS: dict[str, DriverSpec] = {
    "postgres": DriverSpec(dialect="postgresql+psycopg2", module="psycopg2", default_port=5432, verified=True),
    "sqlite": DriverSpec(dialect="sqlite", module="sqlite3", default_port=None, verified=True,
                         notes="File must be inside your business folder on the server (CONNECTOR_FILE_ROOT/<tenant>/)."),
    "mysql": DriverSpec(dialect="mysql+pymysql", module="pymysql", default_port=3306, verified=False),
    "mssql": DriverSpec(dialect="mssql+pyodbc", module="pyodbc", default_port=1433, verified=False,
                        notes="Needs the Microsoft ODBC driver on the server; set odbc_driver if not 'ODBC Driver 18 for SQL Server'. "
                              "SQL Server has no read-only session switch: also give the connection a read-only login."),
    "oracle": DriverSpec(dialect="oracle+oracledb", module="oracledb", default_port=1521, verified=False,
                         notes="'database' is the service name."),
    "odbc": DriverSpec(dialect=None, module="pyodbc", default_port=None, verified=False,
                       notes="Generic ODBC through a DSN or driver name. Read-only is enforced only by the login you provide."),
}


def driver_catalog() -> list[dict]:
    import importlib.util
    return [{"db_type": k, "available": importlib.util.find_spec(v.module) is not None, "verified": v.verified,
             "default_port": v.default_port, "notes": v.notes} for k, v in DRIVERS.items()]


class DatabaseConnectionConfig(BaseModel):
    db_type: str          # a key of DRIVERS
    host: str = ""
    port: Optional[int] = None
    database: str = ""    # for sqlite: path inside the business folder; for oracle: service name
    username: str = ""
    password: str = ""     # encrypted at rest - see app/core/secrets.py
    query: str              # fixed SELECT, configured once, not per-request
    odbc_driver: Optional[str] = None
    dsn: Optional[str] = None          # odbc only
    timeout_seconds: int = 60


class DatabaseColumnMapping(BaseModel):
    """Same role as file_connector.ColumnMapping - maps query result columns onto the Universal Data Contract."""
    entity: str
    event_type: str
    timestamp_col: str
    amount_col: Optional[str] = None
    quantity_col: Optional[str] = None
    currency_col: Optional[str] = None
    location_col: Optional[str] = None
    customer_id_col: Optional[str] = None
    product_id_col: Optional[str] = None
    entity_id_col: Optional[str] = None
    default_currency: str = "KES"


MAX_DB_ROWS = 100_000
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|merge|call|exec|execute|into|copy|vacuum|"
    r"set|commit|rollback|pragma|attach|detach|load_extension|replace|lock|do)\b")


def _strip_literals_and_comments(q: str) -> str:
    q = re.sub(r"/\*.*?\*/", " ", q, flags=re.S)
    q = re.sub(r"--[^\n]*", " ", q)
    q = re.sub(r"'(?:[^']|'')*'", "''", q)
    q = re.sub(r'"(?:[^"]|"")*"', '""', q)
    q = re.sub(r"`[^`]*`", "``", q)
    return q


def _validate_read_only_query(query: str):
    """
    Cheap but real guard: a single SELECT (or WITH ... SELECT), no write/DDL/session keywords, no second statement.
    Comments and string literals are removed first, so a value like 'delete' in a WHERE clause is fine and a
    comment cannot smuggle a statement past the check. NOT a substitute for a read-only database login (the
    business should configure that too) - the connection is additionally put in read-only mode where the database supports it.
    """
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(400, "Database connector queries must be read-only SELECT statements")
    cleaned = _strip_literals_and_comments(query).strip().rstrip(";").strip().lower()
    if not (cleaned.startswith("select") or cleaned.startswith("with")):
        raise HTTPException(400, "Database connector queries must be read-only SELECT statements")
    if ";" in cleaned:
        raise HTTPException(400, "Query contains multiple statements")
    m = _FORBIDDEN.search(cleaned)
    if m:
        raise HTTPException(400, f"Query contains a forbidden keyword ('{m.group(1)}'); only read-only SELECT statements are allowed")


def _spec(config: DatabaseConnectionConfig) -> DriverSpec:
    spec = DRIVERS.get(config.db_type)
    if spec is None:
        raise HTTPException(400, f"Unsupported db_type '{config.db_type}'. Supported: {list(DRIVERS)}")
    import importlib.util
    if importlib.util.find_spec(spec.module) is None:
        raise HTTPException(501, f"The '{spec.module}' driver needed for {config.db_type} is not installed on this server")
    return spec


def build_url(config: DatabaseConnectionConfig, tenant_id: Optional[str] = None) -> URL:
    spec = _spec(config)
    if config.db_type == "sqlite":
        if not tenant_id:
            raise HTTPException(400, "SQLite needs a tenant context")
        path = tenant_path(tenant_id, config.database)
        return URL.create("sqlite", database=str(path))
    check_host_allowed(config.host)
    query = {}
    if config.db_type == "mssql":
        query = {"driver": config.odbc_driver or "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "no"}
    return URL.create(spec.dialect, username=config.username or None, password=config.password or None, host=config.host,
                      port=config.port or spec.default_port, database=config.database or None, query=query)


def _build_engine(config: DatabaseConnectionConfig, tenant_id: Optional[str] = None) -> Engine:
    url = build_url(config, tenant_id)
    args = {} if config.db_type in ("sqlite", "oracle") else {"connect_timeout": 10} if config.db_type in ("postgres", "mysql") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=args)


def odbc_connection_string(config: DatabaseConnectionConfig) -> str:
    """Generic ODBC: values are wrapped in braces with '}' doubled, so a value can never inject extra attributes."""
    def val(v: str) -> str:
        return "{" + str(v).replace("}", "}}") + "}"
    if config.dsn:
        parts = [f"DSN={val(config.dsn)}"]
    elif config.odbc_driver and config.host:
        parts = [f"DRIVER={val(config.odbc_driver)}", f"SERVER={val(config.host)}"]
        if config.port:
            parts.append(f"PORT={config.port}")
        if config.database:
            parts.append(f"DATABASE={val(config.database)}")
    else:
        raise HTTPException(400, "ODBC needs either a dsn, or odbc_driver and host")
    if config.username:
        parts.append(f"UID={val(config.username)}")
    if config.password:
        parts.append(f"PWD={val(config.password)}")
    return ";".join(parts)


_READ_ONLY_SQL = {
    "postgres": ["SET TRANSACTION READ ONLY"],
    "mysql": ["SET SESSION TRANSACTION READ ONLY"],
    "oracle": ["SET TRANSACTION READ ONLY"],
    "sqlite": ["PRAGMA query_only = ON"],
}


def fetch_rows(connector_config: dict, tenant_id: Optional[str] = None) -> list[dict]:
    """
    connector_config is the connector's full config dict, WITH secrets still encrypted (as stored) - decrypted only
    here, at the moment of use, never earlier and never returned to a caller.
    """
    decrypted = decrypt_config_secrets(connector_config)
    try:
        conn_config = DatabaseConnectionConfig(**{k: v for k, v in decrypted.items() if k in DatabaseConnectionConfig.model_fields})
    except Exception as e:
        raise HTTPException(400, f"Database connector config is invalid: {e}")

    _validate_read_only_query(conn_config.query)
    if conn_config.db_type == "odbc":
        return _fetch_odbc(conn_config)
    engine = _build_engine(conn_config, tenant_id)
    try:
        with engine.connect() as conn:
            for stmt in _READ_ONLY_SQL.get(conn_config.db_type, []):
                conn.execute(text(stmt))
            if conn_config.db_type == "postgres":
                conn.execute(text(f"SET LOCAL statement_timeout = {int(conn_config.timeout_seconds) * 1000}"))
            result = conn.execute(text(conn_config.query))
            keys = list(result.keys())
            fetched = result.fetchmany(MAX_DB_ROWS + 1)
            rows = [dict(zip(keys, r)) for r in fetched]
            conn.rollback()
    except HTTPException:
        raise
    except Exception as e:
        # Don't leak connection string / credentials in the error.
        raise HTTPException(502, f"Could not query the connected database: {type(e).__name__}")
    finally:
        engine.dispose()
    if len(rows) > MAX_DB_ROWS:
        raise HTTPException(413, f"The query returned more than {MAX_DB_ROWS} rows; narrow it (for example to recent rows)")
    return rows


def _fetch_odbc(config: DatabaseConnectionConfig) -> list[dict]:
    _spec(config)
    if config.host:
        check_host_allowed(config.host)
    import pyodbc  # imported lazily: optional dependency
    try:
        conn = pyodbc.connect(odbc_connection_string(config), timeout=10, readonly=True)
        try:
            cur = conn.cursor()
            cur.execute(config.query)
            cols = [c[0] for c in cur.description]
            fetched = cur.fetchmany(MAX_DB_ROWS + 1)
        finally:
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Could not query the connected database: {type(e).__name__}")
    if len(fetched) > MAX_DB_ROWS:
        raise HTTPException(413, f"The query returned more than {MAX_DB_ROWS} rows; narrow it")
    return [dict(zip(cols, r)) for r in fetched]


def rows_to_normalized_events(
    rows: list[dict], mapping: DatabaseColumnMapping, tenant_id: str, connector_id: str, source: str
) -> list[dict]:
    if mapping.timestamp_col not in (rows[0].keys() if rows else []):
        if rows:
            raise HTTPException(
                400, f"Configured timestamp_col '{mapping.timestamp_col}' not in query results. "
                     f"Columns returned: {list(rows[0].keys())}"
            )

    events = []
    for row in rows:
        ts_val = row.get(mapping.timestamp_col)
        if isinstance(ts_val, str):
            try:
                ts_val = datetime.fromisoformat(ts_val)
            except ValueError:
                raise HTTPException(400, f"Unparseable timestamp value: {ts_val!r}")
        if not isinstance(ts_val, datetime):
            raise HTTPException(400, f"timestamp_col value is not a date/time: {ts_val!r}")

        def _get(col_attr):
            col = getattr(mapping, col_attr)
            return row.get(col) if col else None

        events.append({
            "tenant_id": tenant_id, "connector_id": connector_id, "source": source,
            "entity": mapping.entity, "entity_id": str(_get("entity_id_col")) if _get("entity_id_col") is not None else None,
            "event_type": mapping.event_type, "timestamp": ts_val,
            "amount": to_number(_get("amount_col"), "amount"),
            "quantity": to_number(_get("quantity_col"), "quantity"),
            "currency": str(_get("currency_col")) if _get("currency_col") is not None else mapping.default_currency,
            "location": str(_get("location_col")) if _get("location_col") is not None else None,
            "customer_id": str(_get("customer_id_col")) if _get("customer_id_col") is not None else None,
            "product_id": str(_get("product_id_col")) if _get("product_id_col") is not None else None,
            "metadata_json": {},
        })
    return events
