"""The advisor feed: only licensed-from-a-lead businesses that switched it on, only serious open alerts, only the headline, only with the key."""
import uuid

import pytest

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.alerts import Alert, AlertSeverity, AlertState
from tests.conftest import bearer
from tests import test_licensing
from tests.test_licensing import activate, issue

master = test_licensing.master            # the same fixture: a signed-in platform owner

KEY = {"X-Advisor-Key": "feed-key-for-tests"}


@pytest.fixture(autouse=True)
def feed_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "ADVISOR_FEED_KEY", "feed-key-for-tests")


def licensed_business(client, h, ref):
    k = issue(client, h, label="Test shop", **({"ref": ref} if ref else {}))
    r = activate(client, k["key"])
    assert r.status_code == 200, r.text
    me = client.get("/api/tenants/me", headers=bearer(r.json())).json()
    return me["id"], bearer(r.json()), k["id"]


def raise_alert(tenant_id, title, severity=AlertSeverity.high, state=AlertState.detected):
    with SessionLocal() as db:
        a = Alert(tenant_id=tenant_id, severity=severity, state=state, title=title, what_happened="private detail 123456", evidence={})
        db.add(a); db.commit()


def feed(client, **params):
    r = client.get("/api/advisor/alerts", params=params, headers=KEY)
    assert r.status_code == 200, r.text
    return r.json()["alerts"]


def test_the_feed_does_not_exist_without_the_key(client, monkeypatch):
    assert client.get("/api/advisor/alerts").status_code == 404
    assert client.get("/api/advisor/alerts", headers={"X-Advisor-Key": "wrong"}).status_code == 404
    monkeypatch.setattr(get_settings(), "ADVISOR_FEED_KEY", "")
    assert client.get("/api/advisor/alerts", headers={"X-Advisor-Key": ""}).status_code == 404       # unset key: nothing opens it
    assert client.get("/api/advisor/alerts", headers=KEY).status_code == 404


def test_off_by_default_and_only_after_the_business_turns_it_on(client, master):
    ref = "lead-" + uuid.uuid4().hex[:6]
    tid, h, lic_id = licensed_business(client, master[1], ref)
    assert client.get("/api/tenants/me", headers=h).json()["share_alerts_with_advisor"] is False
    raise_alert(tid, "Westlands sales are down 15%")
    assert not [a for a in feed(client) if a["ref"] == ref]
    assert client.patch("/api/tenants/me", json={"share_alerts_with_advisor": True}, headers=h).json()["share_alerts_with_advisor"] is True
    got = [a for a in feed(client) if a["ref"] == ref]
    assert len(got) == 1 and got[0]["headline"] == "Westlands sales are down 15%" and got[0]["severity"] == "high" and got[0]["business"]
    assert set(got[0]) == {"id", "ref", "business", "severity", "headline", "at"}            # nothing else leaves: no details, evidence or figures
    assert "private detail" not in str(got)
    client.patch("/api/tenants/me", json={"share_alerts_with_advisor": False}, headers=h)    # switched off again: gone at once
    assert not [a for a in feed(client) if a["ref"] == ref]


def test_only_serious_open_alerts_of_opted_in_licensed_businesses(client, master):
    ref = "lead-" + uuid.uuid4().hex[:6]
    tid, h, lic_id = licensed_business(client, master[1], ref)
    client.patch("/api/tenants/me", json={"share_alerts_with_advisor": True}, headers=h)
    raise_alert(tid, "serious one", AlertSeverity.critical)
    raise_alert(tid, "just medium", AlertSeverity.medium)
    raise_alert(tid, "good news", AlertSeverity.opportunity)
    raise_alert(tid, "already solved", AlertSeverity.high, AlertState.resolved)
    raise_alert(tid, "dismissed", AlertSeverity.high, AlertState.dismissed)
    assert [a["headline"] for a in feed(client) if a["ref"] == ref] == ["serious one"]


def test_a_licence_without_a_lead_reference_is_never_shared_even_if_switched_on(client, master):
    tid, h, lic_id = licensed_business(client, master[1], None)
    client.patch("/api/tenants/me", json={"share_alerts_with_advisor": True}, headers=h)
    raise_alert(tid, "unreferenced")
    assert "unreferenced" not in str(feed(client))


def test_since_returns_only_newer_alerts_and_bad_dates_are_refused(client, master):
    ref = "lead-" + uuid.uuid4().hex[:6]
    tid, h, lic_id = licensed_business(client, master[1], ref)
    client.patch("/api/tenants/me", json={"share_alerts_with_advisor": True}, headers=h)
    raise_alert(tid, "first")
    first = [a for a in feed(client) if a["ref"] == ref]
    assert len(first) == 1
    assert not [a for a in feed(client, since=first[0]["at"]) if a["ref"] == ref]
    raise_alert(tid, "second")
    assert [a["headline"] for a in feed(client, since=first[0]["at"]) if a["ref"] == ref] == ["second"]
    assert client.get("/api/advisor/alerts", params={"since": "yesterday"}, headers=KEY).status_code == 400


def test_a_bad_switch_value_is_refused_and_a_revoked_licence_is_not_shared(client, master):
    ref = "lead-" + uuid.uuid4().hex[:6]
    tid, h, lic_id = licensed_business(client, master[1], ref)
    client.patch("/api/tenants/me", json={"share_alerts_with_advisor": True}, headers=h)
    assert client.patch("/api/tenants/me", json={"share_alerts_with_advisor": None}, headers=h).status_code == 400
    raise_alert(tid, "serious")
    assert [a for a in feed(client) if a["ref"] == ref]
    assert client.post(f"/api/master/licences/{lic_id}/revoke", json={"reason": "test"}, headers=master[1]).status_code == 200
    assert not [a for a in feed(client) if a["ref"] == ref]
