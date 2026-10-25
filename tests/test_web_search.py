"""Web search for questions the business's own data cannot answer."""
import json

import pytest

from app.core import business_questions as bq
from app.core.config import get_settings
from app.core.db import SessionLocal
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
