"""First-boot provisioning (app/core/autoprovision.py).

Runs in a throwaway Postgres schema, so it needs an EMPTY set of tables and never touches the shared test data.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core import autoprovision
from app.core.auth_users import verify_password
from app.core.db import engine
from app.models.config import MonitoredMetric
from app.models.core import Base, Tenant, User
from app.models.licence import Licence


@pytest.fixture()
def scratch(monkeypatch):
    schema = "t_" + uuid.uuid4().hex[:10]
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = engine.execution_options(schema_translate_map={None: schema})
    tables = [t.__table__ for t in (Tenant, User, MonitoredMetric, Licence)]
    needed = {t for table in tables for fk in table.foreign_keys for t in [fk.column.table]} | set(tables)
    Base.metadata.create_all(scoped, tables=[t for t in Base.metadata.sorted_tables if t in needed])
    Session = sessionmaker(autocommit=False, autoflush=False, bind=scoped)
    monkeypatch.setattr(autoprovision, "SessionLocal", Session)
    yield Session
    with engine.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def _counts(Session):
    db = Session()
    try:
        return db.query(Tenant).count(), db.query(User).count(), db.query(Licence).count()
    finally:
        db.close()


def _set_owner(monkeypatch, email="owner@example.com", password="CorrectHorse9"):
    monkeypatch.setenv("OWNER_EMAIL", email)
    monkeypatch.setenv("OWNER_PASSWORD", password)
    monkeypatch.setenv("OWNER_BUSINESS_NAME", "DericBI")


def test_nothing_happens_without_owner_settings(scratch, monkeypatch):
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    monkeypatch.delenv("OWNER_PASSWORD", raising=False)
    assert autoprovision.provision_owner_if_empty() is False
    assert _counts(scratch) == (0, 0, 0)


def test_creates_business_owner_master_and_free_licence_in_an_empty_database(scratch, monkeypatch):
    _set_owner(monkeypatch)
    assert autoprovision.provision_owner_if_empty() is True
    assert _counts(scratch) == (1, 1, 1)
    db = scratch()
    try:
        user, lic, tenant = db.query(User).one(), db.query(Licence).one(), db.query(Tenant).one()
        assert tenant.name == "DericBI"
        assert user.role == "owner" and user.is_master is True and user.must_change_password is False
        assert verify_password("CorrectHorse9", user.password_hash)
        assert lic.status == "active" and lic.price_cents == 0 and lic.tenant_id == tenant.id == user.tenant_id
        assert db.query(MonitoredMetric).filter(MonitoredMetric.tenant_id == tenant.id).count() == 2
    finally:
        db.close()


def test_is_safe_to_run_on_every_start(scratch, monkeypatch):
    _set_owner(monkeypatch)
    assert autoprovision.provision_owner_if_empty() is True
    assert autoprovision.provision_owner_if_empty() is False
    assert autoprovision.provision_owner_if_empty() is False
    assert _counts(scratch) == (1, 1, 1)


def test_never_touches_a_database_that_already_has_a_business(scratch, monkeypatch):
    db = scratch()
    db.add(Tenant(name="Existing")); db.commit(); db.close()
    _set_owner(monkeypatch)
    assert autoprovision.provision_owner_if_empty() is False
    assert _counts(scratch) == (1, 0, 0)


def test_a_weak_password_is_refused_not_stored(scratch, monkeypatch):
    _set_owner(monkeypatch, password="short")
    assert autoprovision.provision_owner_if_empty() is False
    assert _counts(scratch) == (0, 0, 0)


def test_a_failure_never_stops_the_app_from_starting(monkeypatch):
    _set_owner(monkeypatch)
    def boom():
        raise RuntimeError("database unreachable")
    monkeypatch.setattr(autoprovision, "SessionLocal", boom)
    assert autoprovision.provision_owner_if_empty() is False
