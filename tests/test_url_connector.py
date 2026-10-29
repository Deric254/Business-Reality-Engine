"""Web link / REST API connector, inspect (guided mapping), row-id dedupe, SSRF guard, Google Sheets link handling."""
import datetime as dt
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.connectors import url_connector as uc
from app.connectors.inspect import suggest_mapping
from app.core.db import SessionLocal
from app.models.core import BusinessEvent
from tests.test_block_c import make_owner, mk, events_for  # noqa: F401  (fixtures/helpers)

CSV = "Date,Receipt No,Branch,Customer,Total (KES)\n2026-01-01 09:00,R1,Meru,Alice,100\n2026-01-02 10:00,R2,Meru,Bob,250.5\n"
_D = [(dt.datetime.utcnow() - dt.timedelta(days=n)).strftime("%d/%m/%Y") for n in (1, 2, 3)]
MESSY = f"Date,Receipt,Amount\n{_D[0]},A1,\"KES 1,000\"\n,,\n{_D[1]},A2,500\n{_D[2]},A3,-\nTOTAL,,1500\n"
ORDERS = [{"id": i, "date_created": f"2026-01-{(i % 9) + 1:02d}T10:00:00", "total": str(10 * i), "currency": "KES",
           "billing": {"first_name": f"C{i}"}} for i in range(1, 8)]


SHEET = {"csv": "", "json": "[]"}


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/sales.csv":
            body, ct = CSV.encode(), "text/csv"
        elif u.path == "/sheet.csv":                      # a live sheet: tests edit it between syncs
            body, ct = SHEET["csv"].encode(), "text/csv"
        elif u.path == "/sheet.json":
            body, ct = SHEET["json"].encode(), "application/json"
        elif u.path == "/messy.csv":
            body, ct = MESSY.encode(), "text/csv"
        elif u.path == "/orders":
            if self.headers.get("X-Key") != "secret":
                self.send_response(401); self.end_headers(); return
            page, per = int(q.get("page", ["1"])[0]), int(q.get("per_page", ["100"])[0])
            chunk = ORDERS[(page - 1) * per: page * per]
            body, ct = json.dumps({"orders": chunk}).encode(), "application/json"
        elif u.path == "/ignores-paging":
            body, ct = json.dumps(ORDERS[:3]).encode(), "application/json"
        elif u.path == "/redir":
            self.send_response(302); self.send_header("Location", "/sales.csv"); self.end_headers(); return
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def allow_http(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_WEBHOOK_TARGETS", "true")


@pytest.fixture()
def db():
    s = SessionLocal(); yield s; s.close()


def test_google_sheet_share_link_becomes_csv_export():
    u = uc.normalize_url("https://docs.google.com/spreadsheets/d/AbC_123-x/edit?usp=sharing#gid=456")
    assert u == "https://docs.google.com/spreadsheets/d/AbC_123-x/export?format=csv&gid=456"
    assert uc.normalize_url("https://docs.google.com/spreadsheets/d/AbC/edit").endswith("export?format=csv")
    assert "dl=1" in uc.normalize_url("https://www.dropbox.com/s/abc/sales.csv?dl=0")


def test_url_rules(monkeypatch):
    monkeypatch.delenv("ALLOW_PRIVATE_WEBHOOK_TARGETS")
    with pytest.raises(Exception):
        uc.validate_url("http://example.com/x.csv")                  # plain http refused
    with pytest.raises(Exception):
        uc.validate_url("https://user:pw@example.com/x.csv")         # no credentials in the address
    monkeypatch.setenv("BLOCK_PRIVATE_CONNECTOR_HOSTS", "true")
    with pytest.raises(Exception):
        uc.validate_url("https://127.0.0.1/x.csv")                   # internal target refused


def test_suggested_mapping_from_messy_headers():
    import pandas as pd
    df = pd.read_csv(io.StringIO(CSV))
    m = suggest_mapping(df)
    assert m["timestamp_col"] == "Date" and m["amount_col"] == "Total (KES)" and m["entity_id_col"] == "Receipt No"
    assert m["location_col"] == "Branch" and m["customer_id_col"] == "Customer" and m["entity"] == "sale"


SWAHILI = "Tarehe,Mauzo,Duka,Maelezo\n2026-09-01,1500,Nairobi,Unga\n2026-09-02,2300,Mombasa,Sukari\n"


class _FakeAI:
    """Stands in for the AI provider; replies with whatever text it was given."""
    name = "fake"
    def __init__(self, reply): self.reply = reply
    def generate_json(self, system_prompt, user_content, max_tokens=400):
        self.sent = user_content
        if isinstance(self.reply, Exception): raise self.reply
        return self.reply


def test_ai_maps_headers_the_rules_cannot_read_and_hallucinations_are_dropped():
    import pandas as pd
    from app.connectors.inspect import describe
    df = pd.read_csv(io.StringIO(SWAHILI))
    rules = describe(df)
    assert rules["guessed"] == ["timestamp_col", "amount_col"]                             # rules alone cannot read "Tarehe" or "Mauzo": they only guess them from the contents, and say so
    ai = _FakeAI('Here you go: {"mapping": {"timestamp_col": "Tarehe", "amount_col": "Mauzo", "location_col": "Duka", '
                 '"product_id_col": "Maelezo", "customer_id_col": "Client Name", "quantity_col": "Tarehe"}, '
                 '"entity": "sale", "default_currency": "KES", "reasons": {"amount_col": "Mauzo means sales", "location_col": "Duka means shop"}}')
    out = describe(df, ai)
    m = out["suggested_mapping"]
    assert out["source"] == "ai" and m["amount_col"] == "Mauzo" and m["location_col"] == "Duka" and m["timestamp_col"] == "Tarehe"
    assert out["guessed"] == []                                                            # the AI read it by meaning, so it is no longer a guess
    assert "customer_id_col" not in m and "quantity_col" not in m                          # invented column / column already used
    assert m["default_currency"] == "KES" and m["event_type"] == "sale.created"
    assert out["reasons"] == {"amount_col": "Mauzo means sales", "location_col": "Duka means shop"}
    assert "Mauzo" in ai.sent and "1500" in ai.sent                                        # it saw the headers and sample rows


@pytest.mark.parametrize("reply", ["not json at all", RuntimeError("provider down"), '{"mapping": 5}'])
def test_unusable_ai_answer_falls_back_to_the_rules(reply):
    import pandas as pd
    from app.connectors.inspect import describe
    out = describe(pd.read_csv(io.StringIO(CSV)), _FakeAI(reply))
    assert out["source"] == "rules" and out["reasons"] == {} and out["suggested_mapping"]["amount_col"] == "Total (KES)"


def test_ai_cannot_put_a_non_numeric_column_in_amount():
    import pandas as pd
    from app.connectors.inspect import describe
    out = describe(pd.read_csv(io.StringIO(CSV)), _FakeAI('{"mapping": {"amount_col": "Branch"}}'))
    assert out["suggested_mapping"]["amount_col"] == "Total (KES)"                          # the rule's answer stands


def test_inspect_file_uses_the_business_ai_when_connected(client, monkeypatch):
    t, h = make_owner(client, "ai1")
    monkeypatch.setattr("app.connectors.inspect.get_provider", lambda db, tenant: _FakeAI(
        '{"mapping": {"timestamp_col": "Tarehe", "amount_col": "Mauzo"}, "entity": "sale", "reasons": {"amount_col": "sales"}}'))
    r = client.post("/api/connectors/inspect-file", data={"tenant_id": t}, files={"file": ("m.csv", SWAHILI.encode(), "text/csv")}, headers=h)
    assert r.status_code == 200 and r.json()["source"] == "ai" and r.json()["suggested_mapping"]["amount_col"] == "Mauzo"


def test_inspect_url_and_sync_csv_is_idempotent(client, server, db):
    t, h = make_owner(client, "url1")
    cfg = {"url": f"{server}/sales.csv"}
    r = client.post("/api/connectors/inspect-url", json={"tenant_id": t, "config": cfg}, headers=h)
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["row_count"] == 2 and info["suggested_mapping"]["amount_col"] == "Total (KES)"
    assert mk(client, t, h, connector_type="url", config=cfg).status_code == 400          # mapping is required
    cfg.update(info["suggested_mapping"])
    c = mk(client, t, h, connector_type="url", config=cfg, sync_interval_minutes=15)
    assert c.status_code == 200, c.text
    cid = c.json()["id"]
    first = client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    assert first.status_code == 200 and first.json()["new_rows"] == 2, first.text
    again = client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h).json()
    assert again["new_rows"] == 0 and again["duplicates"] == 2
    assert sorted(float(e.amount) for e in events_for(db, cid)) == [100.0, 250.5]


def test_rest_api_with_key_paging_and_wrapper_path(client, server, db):
    t, h = make_owner(client, "url2")
    base = {"url": f"{server}/orders", "auth_type": "header", "auth_header_name": "X-Key", "password": "secret",
            "json_path": "orders", "paginate": "page", "page_size": 3, "timestamp_col": "date_created", "amount_col": "total",
            "customer_id_col": "billing.first_name", "currency_col": "currency", "entity_id_col": "id", "entity": "sale", "event_type": "sale.created"}
    c = mk(client, t, h, connector_type="url", config=base).json()
    from app.models.core import Connector
    assert Connector  # stored secret must be encrypted
    assert db.get(Connector, c["id"]).config["password"].startswith("gAAAA")
    assert "secret" not in json.dumps(client.get(f"/api/connectors/{c['id']}?tenant_id={t}", headers=h).json())
    res = client.post(f"/api/connectors/{c['id']}/sync-url?tenant_id={t}", headers=h).json()
    assert res["rows_fetched"] == 7 and res["new_rows"] == 7                      # 3 pages of 3,3,1


def test_wrong_key_is_a_clear_failure_and_recorded(client, server, db):
    t, h = make_owner(client, "url3")
    cfg = {"url": f"{server}/orders", "auth_type": "header", "auth_header_name": "X-Key", "password": "nope", "json_path": "orders",
           "timestamp_col": "date_created", "amount_col": "total", "entity": "sale", "event_type": "sale.created"}
    cid = mk(client, t, h, connector_type="url", config=cfg).json()["id"]
    r = client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    assert r.status_code == 502 and "refused access" in r.text, r.text
    from app.models.core import Connector
    db.expire_all()
    assert db.get(Connector, cid).last_sync_status == "failed"


def test_api_that_ignores_paging_does_not_loop(server):
    df = uc.fetch_dataframe({"url": f"{server}/ignores-paging", "paginate": "page", "page_size": 2, "max_pages": 10})
    assert len(df) == 3


def test_redirect_is_followed(server):
    assert len(uc.fetch_dataframe({"url": f"{server}/redir"})) == 2


def test_upload_twice_does_not_double_count(client, db):
    t, h = make_owner(client, "url4")
    cfg = {"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total (KES)"}   # NO id column mapped
    cid = mk(client, t, h, connector_type="file", config=cfg).json()["id"]
    for _ in range(2):
        r = client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", CSV.encode(), "text/csv")}, headers=h)
        assert r.status_code == 200, r.text
    assert len(events_for(db, cid)) == 2


def test_messy_sheet_link_imports_every_readable_row_on_the_right_days(client, server):
    t, h = make_owner(client, "messy")
    cfg = {"url": f"{server}/messy.csv", "auth_type": "none", "entity": "sale", "event_type": "sale.created", "timestamp_col": "Date",
           "amount_col": "Amount", "entity_id_col": "Receipt"}
    cid = mk(client, t, h, connector_type="url", config=cfg).json()["id"]
    r = client.post(f"/api/connectors/{cid}/sync-url?tenant_id={t}", headers=h)
    assert r.status_code == 200 and r.json()["new_rows"] == 3 and r.json()["skipped"] == 1, r.text
    assert r.json()["skipped_examples"] == ["Row 6: 'TOTAL' is not a date"]
    assert _revenue_30(client, t, h) == 1500                                                       # all three land in the last 30 days


def _revenue_30(client, t, h):
    return client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()["revenue"]["current"]


def test_inspect_file_stores_nothing(client, db):
    t, h = make_owner(client, "url5")
    r = client.post("/api/connectors/inspect-file", data={"tenant_id": t}, files={"file": ("s.csv", CSV.encode(), "text/csv")}, headers=h)
    assert r.status_code == 200 and r.json()["suggested_mapping"]["timestamp_col"] == "Date"
    assert db.query(BusinessEvent).filter(BusinessEvent.tenant_id == t).count() == 0


def test_setup_status_is_public(client):
    r = client.get("/api/health/setup")
    assert r.status_code == 200 and r.json()["has_business"] is True


def test_trend_series_zero_filled(client):
    t, h = make_owner(client, "trend")
    cid = mk(client, t, h, connector_type="file", config={"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total"}).json()["id"]
    rows = "".join(f"{dt.datetime.utcnow() - dt.timedelta(days=d):%Y-%m-%d} 10:00,{100 + d}\n" for d in range(1, 55))
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", ("Date,Total\n" + rows).encode(), "text/csv")}, headers=h).status_code == 200
    r = client.get(f"/api/business/trend?tenant_id={t}&days=30", headers=h).json()
    assert len(r["current"]) == 30 and len(r["previous"]) == 30
    assert sum(x["sales"] for x in r["previous"]) > 0 and r["current"][-1]["date"] == (dt.datetime.utcnow().date() - dt.timedelta(days=1)).isoformat()      # complete days: ends yesterday


def test_dashboard_numbers_refresh_right_after_new_data(client):
    t, h = make_owner(client, "cache")
    assert client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()["revenue"]["current"] == 0      # caches the empty answer
    cid = mk(client, t, h, connector_type="file", config={"entity": "sale", "event_type": "sale.created", "timestamp_col": "Date", "amount_col": "Total (KES)"}).json()["id"]
    csv_now = f"Date,Total (KES)\n{dt.datetime.utcnow() - dt.timedelta(days=1):%Y-%m-%d %H:%M},500\n"
    assert client.post(f"/api/connectors/{cid}/upload", data={"tenant_id": t}, files={"file": ("s.csv", csv_now.encode(), "text/csv")}, headers=h).status_code == 200
    assert client.get(f"/api/questions/kpis?tenant_id={t}&period_days=30", headers=h).json()["revenue"]["current"] == 500
