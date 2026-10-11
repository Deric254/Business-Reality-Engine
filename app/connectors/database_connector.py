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

Tested end-to-end against a real Postgres database only. The mysql+pymysql
driver path is wired up and pymysql is installed, but untested — no MySQL
server was available to verify against in this environment. Treat the
MySQL path as unverified until it's actually been run against one.
"""
from typing import Optional
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from pydantic import BaseModel
from fastapi import HTTPException

from app.core.secrets import decrypt_config_secrets

# Fixed allowlist — a connector's db_type must be one of these, never an
# arbitrary driver string from user input (that would let a connector
# config smuggle in an unexpected SQLAlchemy dialect/driver).
_DRIVER_BY_TYPE = {
    "postgres": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
}


class DatabaseConnectionConfig(BaseModel):
    db_type: str          # "postgres" | "mysql"
    host: str
    port: int
    database: str
    username: str
    password: str          # encrypted at rest — see app/core/secrets.py
    query: str              # fixed SELECT, configured once, not per-request


class DatabaseColumnMapping(BaseModel):
    """Same role as file_connector.ColumnMapping — maps query result
    columns onto the Universal Data Contract."""
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


def _validate_read_only_query(query: str):
    """
    Cheap but real guard: reject anything that isn't a single SELECT.
    Not a substitute for the connection itself being read-only (it should
    also be, at the DB-user-permissions level — that's on the business's
    side to configure), but this stops an obviously wrong/dangerous query
    from ever being attempted.
    """
    normalized = query.strip().lower()
    if not normalized.startswith("select"):
        raise HTTPException(400, "Database connector queries must be read-only SELECT statements")
    forbidden = ["insert ", "update ", "delete ", "drop ", "alter ", "truncate ", "grant ", ";"]
    if any(word in normalized for word in forbidden):
        raise HTTPException(400, "Query contains forbidden keywords or multiple statements")


def _build_engine(config: DatabaseConnectionConfig) -> Engine:
    if config.db_type not in _DRIVER_BY_TYPE:
        raise HTTPException(400, f"Unsupported db_type '{config.db_type}'. Supported: {list(_DRIVER_BY_TYPE)}")
    driver = _DRIVER_BY_TYPE[config.db_type]
    url = f"{driver}://{config.username}:{config.password}@{config.host}:{config.port}/{config.database}"
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})


def fetch_rows(connector_config: dict) -> list[dict]:
    """
    connector_config is the connector's full config dict, WITH secrets
    still encrypted (as stored) — decrypted only here, at the moment of
    use, never earlier and never returned to a caller.
    """
    decrypted = decrypt_config_secrets(connector_config)
    try:
        conn_config = DatabaseConnectionConfig(**decrypted)
    except Exception as e:
        raise HTTPException(400, f"Database connector config is invalid: {e}")

    _validate_read_only_query(conn_config.query)
    engine = _build_engine(conn_config)

    try:
        with engine.connect() as conn:
            result = conn.execute(text(conn_config.query))
            rows = [dict(row._mapping) for row in result]
    except Exception as e:
        # Don't leak connection string / credentials in the error — same
        # principle as every other error handler in this app.
        raise HTTPException(502, f"Could not query the connected database: {type(e).__name__}")
    finally:
        engine.dispose()

    return rows


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
            "amount": float(_get("amount_col")) if _get("amount_col") is not None else None,
            "quantity": float(_get("quantity_col")) if _get("quantity_col") is not None else None,
            "currency": str(_get("currency_col")) if _get("currency_col") is not None else mapping.default_currency,
            "location": str(_get("location_col")) if _get("location_col") is not None else None,
            "customer_id": str(_get("customer_id_col")) if _get("customer_id_col") is not None else None,
            "product_id": str(_get("product_id_col")) if _get("product_id_col") is not None else None,
            "metadata_json": {},
        })
    return events
