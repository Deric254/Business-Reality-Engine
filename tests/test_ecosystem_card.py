"""The capability card needs no database, so it is tested on its own."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ecosystem


def test_card_is_public_valid_and_secret_free():
    app = FastAPI()
    app.include_router(ecosystem.router, prefix="/api/ecosystem")
    r = TestClient(app).get("/api/ecosystem/card")
    assert r.status_code == 200
    c = r.json()
    assert c["contract"] == "dericbi.ecosystem/1" and c["id"] and c["name"]
    assert all(isinstance(x, str) for x in c["offers"] + c["accepts"])
    assert not any(k in str(c).lower() for k in ("token", "secret", "password", "key"))
