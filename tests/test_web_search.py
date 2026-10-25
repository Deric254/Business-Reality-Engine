"""Web search for questions the business's own data cannot answer."""

import pytest

from app.core import business_questions as bq
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.models.core import Tenant
from app.reasoning import web_search
from tests.conftest import _make_tenant

PAGES = [{"title": "Nairobi coffee scene", "url": "https://example.com/a", "snippet": "Java House and Artcaffe lead."},
         {"title": "Market notes", "url": "https://example.com/b", "snippet": "Dormans is growing."}]


class Provider:
    name = "fake"

    def __init__(self, reply="Java House and Artcaffe lead [1].", raises=False):
        self.reply, self.raises, self.calls = reply, raises, []

    def generate_json(self, system_prompt, user_content, max_tokens=400):
        self.calls.append((system_prompt, user_content))
        if self.raises:
            raise RuntimeError("down")
        return self.reply


@pytest.fixture()
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "WEB_SEARCH_API_KEY", "k")
    monkeypatch.setattr(s, "WEB_SEARCH_PROVIDER", "tavily")


class Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_search_is_off_without_a_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "WEB_SEARCH_API_KEY", "")
    monkeypatch.setattr(web_search.httpx, "post", lambda *a, **k: pytest.fail("must not call out"))
    assert web_search.is_configured() is False and web_search.search("anything") == []


def test_tavily_results_are_parsed_and_unsafe_links_dropped(configured, monkeypatch):
    data = {"results": [{"title": "A", "url": "https://a.test/x", "content": "alpha"},
                        {"title": "B", "url": "javascript:alert(1)", "content": "beta"},
                        {"title": "", "url": "https://c.test", "content": "gamma"}]}
    seen = {}
    def post(url, **kw):
        seen.update(url=url, **kw)
        return Resp(data)
    monkeypatch.setattr(web_search.httpx, "post", post)
    assert web_search.search("who sells tea") == [{"title": "A", "url": "https://a.test/x", "snippet": "alpha"}]
    assert seen["json"]["query"] == "who sells tea" and seen["headers"]["Authorization"] == "Bearer k"


def test_brave_results_are_parsed(configured, monkeypatch):
    monkeypatch.setattr(get_settings(), "WEB_SEARCH_PROVIDER", "brave")
    data = {"web": {"results": [{"title": "A", "url": "https://a.test", "description": "alpha"}]}}
    monkeypatch.setattr(web_search.httpx, "get", lambda url, **kw: Resp(data))
    assert web_search.search("q") == [{"title": "A", "url": "https://a.test", "snippet": "alpha"}]


def test_a_failing_search_service_returns_nothing(configured, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("503")
    monkeypatch.setattr(web_search.httpx, "post", boom)
    assert web_search.search("q") == []


def test_unanswerable_question_gets_a_labelled_sourced_web_answer(db, monkeypatch):
    t = _make_tenant("web")
    p = Provider()
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: p)
    monkeypatch.setattr(bq.web_search, "search", lambda q: PAGES)
    out = bq.answer_question(db, t, "zzqx plorv wibble competitors elsewhere")
    assert out["answered"] and out["answer_source"] == "web" and out["intent"] == "web"
    assert [s["url"] for s in out["sources"]] == ["https://example.com/a", "https://example.com/b"]
    assert "Nairobi coffee scene" in p.calls[-1][1] and "untrusted" in p.calls[-1][0]


def test_web_is_not_used_when_the_own_data_can_answer(db, monkeypatch):
    t = _make_tenant("webown")
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: Provider())
    monkeypatch.setattr(bq.web_search, "search", lambda q: pytest.fail("own-data questions must not go to the web"))
    assert bq.answer_question(db, t, "What are my expenses this week?")["answer_source"] != "web"


@pytest.mark.parametrize("pages,provider", [([], Provider()), (PAGES, Provider(raises=True)), (PAGES, Provider(reply="  "))])
def test_web_failure_falls_back_to_the_honest_refusal(db, monkeypatch, pages, provider):
    t = _make_tenant("webfail")
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: provider)
    monkeypatch.setattr(bq.web_search, "search", lambda q: pages)
    out = bq.answer_question(db, t, "zzqx plorv wibble competitors elsewhere")
    assert out["answered"] is False and "from your own numbers" in out["answer"]


def test_the_business_area_is_added_to_the_search_unless_the_question_has_it(db, monkeypatch):
    t = _make_tenant("webarea")
    row = db.get(Tenant, t); row.area = "Westlands, Nairobi"; db.commit()
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: Provider())
    seen = []
    monkeypatch.setattr(bq.web_search, "search", lambda q: seen.append(q) or PAGES)
    out = bq.answer_question(db, t, "zzqx plorv wibble competitors elsewhere")
    bq.answer_question(db, t, "zzqx plorv wibble competitors in westlands, nairobi")
    assert seen == ["zzqx plorv wibble competitors elsewhere Westlands, Nairobi", "zzqx plorv wibble competitors in westlands, nairobi"]
    assert out["answer_source"] == "web"


def test_owner_can_set_and_clear_the_area(client):
    from tests.conftest import bearer, register
    t = _make_tenant("webset"); _, _, owner = register(client, t, "owner")
    r = client.patch("/api/tenants/me", json={"area": "  Kilimani, Nairobi "}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["area"] == "Kilimani, Nairobi"
    assert client.patch("/api/tenants/me", json={"area": "  "}, headers=bearer(owner)).json()["area"] is None
    assert client.patch("/api/tenants/me", json={"area": "x" * 101}, headers=bearer(owner)).status_code == 400


def _sale(db, t, cid, place, product, amount):
    import datetime as dt
    from app.models.core import BusinessEvent
    db.add(BusinessEvent(tenant_id=t, connector_id=cid, source="t", entity="sale", event_type="sale.x",
                         timestamp=dt.datetime.utcnow() - dt.timedelta(days=10), amount=amount, location=place,
                         product_id=product, validation_status="validated"))


def test_competitor_question_searches_for_what_the_business_sells_and_where(db, monkeypatch):
    """No area set: the search is built from the branches and top products in the owner's own records, so it finds
    real businesses nearby instead of articles about how to find competitors."""
    from app.models.core import Connector
    t = _make_tenant("webctx")
    c = Connector(tenant_id=t, name="B", connector_type="file", expected_frequency="periodic", is_active=True)
    db.add(c); db.commit()
    for place, prod, amt in [("Meru Town", "Sugar 2kg", 500), ("Meru Town", "Sugar 2kg", 400), ("Maua", "Rice 2kg", 300), ("Maua", "Cooking Oil 1L", 100)]:
        _sale(db, t, c.id, place, prod, amt)
    db.commit()
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: Provider())
    seen = []
    monkeypatch.setattr(bq.web_search, "search", lambda q: seen.append(q) or PAGES)
    out = bq.answer_question(db, t, "who are my competitors")
    q = seen[0]
    assert "Sugar 2kg" in q and "Meru Town" in q and "Maua" in q and "Kenya" in q and "who are my competitors" not in q
    assert out["search_query"] == q and out["needs_area"] is True
    # only names reach the search: no amount appears in the query
    assert "500" not in q and "1300" not in q


def test_a_competitor_question_goes_straight_to_the_web_without_extra_model_calls(db, monkeypatch):
    t = _make_tenant("webfast")
    p = Provider()
    monkeypatch.setattr(bq, "get_provider", lambda *a, **k: p)
    monkeypatch.setattr(bq.web_search, "search", lambda q: PAGES)
    out = bq.answer_question(db, t, "who are my competitors")
    assert out["answer_source"] == "web"
    assert len(p.calls) == 1          # one model call (the answer), no planning or classifying first


# --- speed and reliability of the AI calls ---------------------------------------------------------------------------

def test_a_failing_provider_is_skipped_for_a_while_so_the_next_question_is_not_slow(monkeypatch):
    from app.reasoning import llm_providers as L
    calls = []

    class Boom(L.AnthropicProvider):
        def __init__(self):
            self._model = "m"; self._health_key = L.health.key("anthropic", "m", "unique-key-1", None)
            class C:
                class messages:
                    @staticmethod
                    def create(**k):
                        calls.append(1); raise TimeoutError("slow")
            self._client = C()

    p = Boom()
    for _ in range(3):
        with pytest.raises(Exception):
            p.generate_json("s", "u")
    assert len(calls) == 1          # the service was tried once; the next two were refused at once


def test_a_rejected_request_does_not_mark_the_service_as_down():
    from app.reasoning import llm_providers as L
    k = L.health.key("openai", "m", "unique-key-2", None)
    class E(Exception):
        status_code = 400
    L.health.failed(k, E())
    assert L.health.is_down(k) is False
    L.health.failed(k, TimeoutError())
    assert L.health.is_down(k) is True
    L.health.ok(k)


def test_the_same_question_twice_is_answered_once_and_failures_are_not_remembered(client, monkeypatch):
    from tests.conftest import bearer
    from app.core import cache
    cache.clear_local()
    from tests.conftest import register
    t = _make_tenant("askcache"); _, _, owner = register(client, t, "owner")
    n = []
    import app.api.questions as Q
    def fake(db, t, q, d):
        n.append(1)
        return {"question": q, "answered": len(n) > 1, "answer": "x", "answer_source": "web"}
    monkeypatch.setattr(Q, "answer_question", fake)
    body = {"tenant_id": t, "question": "Hello there", "period_days": 7}
    h = bearer(owner)
    client.post("/api/questions/ask", json=body, headers=h)            # not answered: must not be kept
    client.post("/api/questions/ask", json=body, headers=h)            # answered: kept
    client.post("/api/questions/ask", json={**body, "question": "  hello   THERE "}, headers=h)   # same question, new spelling
    assert len(n) == 2

