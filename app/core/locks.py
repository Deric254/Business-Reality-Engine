"""Session-level Postgres advisory locks held on their OWN connection.

A lock taken through a request's Session can be lost track of: Session.commit()/rollback() hands the connection back
to the pool, so a later unlock may run on a different connection and the lock leaks. Holding the lock on a dedicated
connection for exactly the duration of the `with` block makes lock and unlock the same connection by construction
(and if the process dies, Postgres releases the lock when the connection closes)."""
from contextlib import contextmanager

from sqlalchemy import text

from app.core.db import engine


@contextmanager
def try_advisory_lock(key: str):
    conn = engine.connect()
    got = False
    try:
        got = bool(conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key}).scalar())
        yield got
    finally:
        try:
            if got:
                conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
            conn.commit()
        finally:
            conn.close()
