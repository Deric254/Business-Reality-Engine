"""In-app AI providers: keys encrypted, never returned, owner only, tested before saving, fallback order."""
import pytest

from app.core.db import SessionLocal
from app.models.config import AIProvider
from app.reasoning import llm_providers as lp
from tests.test_block_c import make_owner  # noqa: F401


class _OK(lp.LLMProvider):
    name = "ok"
    def generate_json(self, s, u, max_tokens=400):
        return "OK"


class _Boom(lp.LLMProvider):
    name = "boom"
    def generate_json(self, s, u, max_tokens=400):
        raise RuntimeError("429 rate limited")


def test_add_list_remove_and_key_never_returned(client, monkeypatch):
    t, h = make_owner(client)
    monkeypatch.setattr("app.api.ai.build_provider", lambda *a, **k: _OK())
    body = {"label": "Groq", "kind": "openai_compatible", "api_key": "gsk_secret_key_123456",
            "model": "llama-3.3-70b-versatile", "base_url": "https://api.groq.com/openai/v1"}
    r = client.post(f"/api/ai/providers?tenant_id={t}", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert "gsk_secret" not in r.text
    pid = r.json()["id"]
    lst = client.get(f"/api/ai/providers?tenant_id={t}", headers=h)
    assert [p["label"] for p in lst.json()] == ["Groq"] and "gsk_secret" not in lst.text
    with SessionLocal() as db:
        stored = db.get(AIProvider, pid).api_key
    assert "gsk_secret" not in stored
    assert client.delete(f"/api/ai/providers/{pid}?tenant_id={t}", headers=h).status_code == 200
    assert client.get(f"/api/ai/providers?tenant_id={t}", headers=h).json() == []


def test_bad_key_is_rejected_and_not_saved(client, monkeypatch):
    t, h = make_owner(client)
    monkeypatch.setattr("app.api.ai.build_provider", lambda *a, **k: _Boom())
    r = client.post(f"/api/ai/providers?tenant_id={t}", headers=h,
                    json={"label": "X", "kind": "openai", "api_key": "sk-badbadbadbad", "model": "gpt-4o-mini"})
    assert r.status_code == 400 and "didn't work" in r.json()["detail"]
    assert client.get(f"/api/ai/providers?tenant_id={t}", headers=h).json() == []


def test_private_base_url_and_plain_http_refused(client, monkeypatch):
    t, h = make_owner(client)
    monkeypatch.setattr("app.api.ai.build_provider", lambda *a, **k: _OK())
    for url in ("http://api.example.com/v1", "https://user:pw@api.example.com/v1"):
        r = client.post(f"/api/ai/providers?tenant_id={t}", headers=h,
                        json={"label": "X", "kind": "openai_compatible", "api_key": "abcdefgh1234", "model": "m", "base_url": url})
        assert r.status_code == 400


def test_fallback_uses_next_provider_and_tenant_rows_beat_env():
    p = lp.FallbackProvider([_Boom(), _OK()])
    assert p.generate_json("s", "u") == "OK"
    with pytest.raises(RuntimeError, match="All connected AI providers failed"):
        lp.FallbackProvider([_Boom(), _Boom()]).generate_json("s", "u")


def test_model_list_reads_provider_and_strips_prefix(client, monkeypatch):
    t, h = make_owner(client)

    class R:
        status_code = 200
        def json(self):
            return {"data": [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-3.8-pro"}]}
    seen = {}
    def fake_get(url, **kw):
        seen["url"], seen["auth"] = url, kw["headers"]["Authorization"]
        return R()
    monkeypatch.setattr("app.api.ai.httpx.get", fake_get)
    r = client.post(f"/api/ai/models?tenant_id={t}", headers=h,
                    json={"kind": "openai_compatible", "api_key": "abcdefgh1234", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"})
    assert r.status_code == 200 and r.json()["models"] == ["gemini-3.8-flash", "gemini-3.8-pro"]
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/models" and seen["auth"] == "Bearer abcdefgh1234"
    bad = client.post(f"/api/ai/models?tenant_id={t}", headers=h, json={"kind": "openai_compatible", "api_key": "abcdefgh1234", "base_url": "http://x.com"})
    assert bad.status_code == 400
