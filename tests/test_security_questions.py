"""Security questions: self-service recovery that needs no email."""
import uuid

from app.core import security_questions as sq
from app.core.db import SessionLocal
from app.models.core import User
from tests.conftest import bearer, register

Q = sq.QUESTIONS[:3]


def _items(a=("Greenfield", "Kisumu", "Wanjiru")):
    return [{"question": q, "answer": x} for q, x in zip(Q, a)]


def _set(client, tok, pw="correct-horse-1", items=None):
    return client.put("/api/auth/security-questions", json={"current_password": pw, "items": items or _items()}, headers=bearer(tok))


def _recover(client, email, answers, pw="brand-new-pass-9"):
    return client.post("/api/auth/recover-with-answers", json={"email": email, "answers": answers, "new_password": pw})


def test_set_and_read_back_never_returns_answers(client, tenant_a):
    email, pw, tok = register(client, tenant_a, "owner")
    got = client.get("/api/auth/security-questions", headers=bearer(tok)).json()
    assert got["options"] == sq.QUESTIONS and got["count"] == 3 and got["questions"] == []
    assert _set(client, tok, "wrong-password").status_code == 401                       # needs the current password
    r = _set(client, tok); assert r.status_code == 200 and r.json() == {"questions": Q}
    assert client.get("/api/auth/security-questions", headers=bearer(tok)).json()["questions"] == Q
    db = SessionLocal(); row = db.query(User).filter(User.email == email).first().security_questions; db.close()
    assert all(r["h"].startswith("$2") and "greenfield" not in str(r).lower() for r in row)   # hashed, nothing readable


def test_questions_must_be_a_good_choice(client, tenant_a):
    _, _, tok = register(client, tenant_a, "owner")
    assert _set(client, tok, items=_items()[:2]).status_code == 400                                      # too few
    assert _set(client, tok, items=[_items()[0]] * 3).status_code == 400                                 # same question
    assert _set(client, tok, items=[{"question": "Made up?", "answer": "abc"}] + _items()[1:]).status_code == 400
    assert _set(client, tok, items=_items(("ab", "Kisumu", "Wanjiru"))).status_code == 400               # too short
    assert _set(client, tok, items=_items(("Same", " same ", "SAME"))).status_code == 400                # same answer


def test_recover_with_answers_end_to_end(client, tenant_a):
    email, pw, tok = register(client, tenant_a, "owner"); _set(client, tok)
    assert client.post("/api/auth/recovery-questions", json={"email": email}).json()["questions"] == Q
    assert _recover(client, email, ["Greenfield", "Kisumu", "Nope"]).status_code == 401                  # one wrong is enough to fail
    assert _recover(client, email, ["Greenfield", "Kisumu"]).status_code == 401                        # missing answer
    assert _recover(client, email, ["Greenfield", "Kisumu", "Wanjiru"], pw="short").status_code == 400  # bad new password, no reset
    assert _recover(client, email, ["  GREENFIELD ", "kisumu", "Wánjiru"]).status_code == 200            # case, spacing, accents ignored
    assert client.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "brand-new-pass-9"}).status_code == 200
    assert client.get("/api/tenants/me", headers=bearer(tok)).status_code == 401                         # old session revoked


def test_unknown_email_and_unset_account_look_alike(client, tenant_a):
    email, _, _ = register(client, tenant_a, "owner")                                                    # no questions set
    ghost = f"nobody-{uuid.uuid4().hex[:6]}@example.com"
    a = client.post("/api/auth/recovery-questions", json={"email": ghost}).json()["questions"]
    assert a == client.post("/api/auth/recovery-questions", json={"email": ghost}).json()["questions"]   # repeatable decoy
    assert len(a) == 3 and all(q in sq.QUESTIONS for q in a)
    r1, r2 = _recover(client, ghost, ["a", "b", "c"]), _recover(client, email, ["a", "b", "c"])
    assert r1.status_code == r2.status_code == 401 and r1.json() == r2.json()


def test_repeated_wrong_answers_lock_recovery(client, tenant_a):
    email, _, tok = register(client, tenant_a, "owner"); _set(client, tok)
    for _ in range(5):
        assert _recover(client, email, ["x", "y", "z"]).status_code == 401
    assert _recover(client, email, ["Greenfield", "Kisumu", "Wanjiru"]).status_code == 429               # even the right answers
    assert client.post("/api/auth/login", json={"email": email, "password": "correct-horse-1"}).status_code == 200   # sign-in unaffected
