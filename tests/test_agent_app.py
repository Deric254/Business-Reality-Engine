"""The one-file agent for ordinary users: a local page to paste the code into, which only this computer's owner can use,
and a loop that picks up systems added while it runs."""
import base64
import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import reality_agent_app as A  # noqa: E402
import systems as S  # noqa: E402

from tests.test_block_c import make_owner  # noqa: E402
from tests.test_local_systems import Bridge, Rig, imara_db  # noqa: E402,F401


def code_for(system="imara", cid="c1", token="t", url="https://engine.example"):
    return "bre1." + base64.urlsafe_b64encode(json.dumps({"u": url, "c": cid, "t": token, "s": system}).encode()).decode().rstrip("=")


@pytest.fixture()
def ui(tmp_path):
    srv = A.build_server(tmp_path, [], port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def call(method, path, body=None, host=None, cookie=True, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": host or f"127.0.0.1:{port}"}
        if cookie:
            h["Cookie"] = f"bre_k={srv.key}"
        if origin:
            h["Origin"] = origin
        if body is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data.decode()), r
    yield call, srv, tmp_path, port
    srv.shutdown()
    srv.server_close()


def test_page_needs_the_key_and_a_local_address(ui):
    call, srv, home, port = ui
    assert call("GET", "/", cookie=False)[0] == 403
    assert call("GET", "/", host="evil.example")[0] == 403                    # a web page reaching in through a rebound name
    status, body, _ = call("GET", "/")
    assert status == 200 and "Paste the code" in body
    status, _, r = call("GET", f"/?k={srv.key}", cookie=False)                # how the agent opens it for the owner
    assert status == 302 and "bre_k=" in r.getheader("Set-Cookie") and "SameSite=Strict" in r.getheader("Set-Cookie")
    assert call("GET", "/?k=wrong", cookie=False)[0] == 403


def test_other_websites_cannot_connect_the_agent(ui):
    call, srv, home, port = ui
    assert call("POST", "/api/add", {"code": code_for()}, origin="https://evil.example")[0] == 403
    assert call("POST", "/api/add", {"code": code_for()}, cookie=False)[0] == 403
    assert not A.config_path(home).exists()


def test_pasting_a_code_connects_and_keeps_it(ui):
    call, srv, home, port = ui
    status, body, _ = call("POST", "/api/add", {"code": code_for()})
    assert status == 200 and "Imara connected" in body["message"]
    cfg = json.loads(A.config_path(home).read_text())
    assert cfg["base_url"] == "https://engine.example" and cfg["systems"][0]["system"] == "imara"
    assert oct(A.config_path(home).stat().st_mode & 0o777) == "0o600"          # it holds the connection token
    st = call("GET", "/api/status")[1]["systems"]
    assert st[0]["label"] == "Imara" and st[0]["state"] == "starting"


def test_bad_codes_get_a_plain_answer(ui):
    call, *_ = ui
    for bad in ("hello", "bre1.@@@", code_for("payroll")):
        status, body, _ = call("POST", "/api/add", {"code": bad})
        assert status == 400 and body["error"]
    assert call("POST", "/api/add", {"code": code_for(url="https://a.example")})[0] == 200
    assert call("POST", "/api/add", {"code": code_for("pharmacy", url="https://b.example")})[0] == 400   # one account per agent


def test_location_and_removal(ui):
    call, srv, home, port = ui
    call("POST", "/api/add", {"code": code_for()})
    assert call("POST", "/api/path", {"system": "imara", "path": "D:\\data\\imara.db"})[0] == 200
    assert json.loads(A.config_path(home).read_text())["systems"][0]["path"] == "D:\\data\\imara.db"
    assert call("POST", "/api/path", {"system": "imara", "path": ""})[0] == 200
    assert "path" not in json.loads(A.config_path(home).read_text())["systems"][0]
    assert call("POST", "/api/path", {"system": "smepro", "path": "x"})[0] == 404
    assert call("POST", "/api/remove", {"system": "imara"})[0] == 200
    assert json.loads(A.config_path(home).read_text())["systems"] == []


def test_a_second_copy_does_not_start(ui):
    call, srv, home, port = ui
    with pytest.raises(OSError):
        A.build_server(home, [], port=port)


def test_a_system_added_while_running_starts_by_itself(client, tmp_path):
    rig = Rig(client, tmp_path, "imara", imara_db)
    cfg = {"base_url": "http://localhost", "systems": [], "state_file": str(tmp_path / "loop.db"), "poll_seconds": 0.05, "heartbeat_seconds": 0, "force_pass_seconds": 0}
    later = {**cfg, "systems": [rig.entry]}
    box = {"cfg": None}
    stop, runners = threading.Event(), []
    import unittest.mock as m
    with m.patch.object(S, "EngineClient", lambda *a, **k: Bridge(client, rig.cid, rig.code["token"])):
        t = threading.Thread(target=S.run_loop, args=(cfg, S.load_profiles(), stop, lambda *a: None),
                             kwargs={"reload": lambda: box.pop("cfg", None) if box.get("cfg") else None, "runners": runners, "config_check_seconds": 0.1})
        t.start()
        time.sleep(0.5)
        assert runners == [] and rig.events() == []
        box["cfg"] = later                                                   # the owner pasted a code
        for _ in range(100):
            if len(rig.events("sale")) == 2:
                break
            time.sleep(0.2)
        assert len(rig.events("sale")) == 2 and [r.entry["system"] for r in runners] == ["imara"]
        box["cfg"] = {**cfg, "systems": []}                                  # removed again: it stops
        for _ in range(50):
            if not runners:
                break
            time.sleep(0.1)
        stop.set()
        t.join(10)
    assert runners == []


def test_startup_command_points_at_this_program():
    cmd = A.startup_command()
    assert cmd.endswith("--background") and "reality_agent_app" in cmd or getattr(sys, "frozen", False)
    assert A.set_startup(True) is False or sys.platform.startswith("win")      # only Windows has this entry


def test_list_offers_the_download_when_the_server_has_one(client, tmp_path, monkeypatch):
    rig = Rig(client, tmp_path, "imara", imara_db)
    rows = client.get(f"/api/agent/?tenant_id={rig.tenant}", headers=rig.h).json()
    assert all(r["download_url"] is None for r in rows)
    monkeypatch.setenv("AGENT_DOWNLOAD_URL", "https://example.com/RealityAgent.exe")
    rows = client.get(f"/api/agent/?tenant_id={rig.tenant}", headers=rig.h).json()
    assert all(r["download_url"] == "https://example.com/RealityAgent.exe" for r in rows)


def test_one_code_connects_several_systems(client, tmp_path):
    tenant, h = make_owner(client, "multi")
    r = client.post("/api/agent/connect", json={"tenant_id": tenant, "systems": ["smepro", "imara", "imara", "pharmacy"]}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"].startswith("bre2.") and len(body["connector_ids"]) == 3        # repeated names count once
    found = S.decode_codes(body["code"])
    assert [d["system"] for d in found] == ["smepro", "imara", "pharmacy"]
    for d in found:                                                                     # each part works on its own
        hb = client.post(f"/api/agent/{d['connector_id']}/heartbeat", json={"status": {"state": "not_found"}}, headers={"Authorization": f"Bearer {d['token']}"})
        assert hb.status_code == 200
    assert client.post("/api/agent/connect", json={"tenant_id": tenant}, headers=h).status_code == 400
    assert client.post("/api/agent/connect", json={"tenant_id": tenant, "systems": ["imara", "payroll"]}, headers=h).status_code == 400
    one = client.post("/api/agent/connect", json={"tenant_id": tenant, "system": "imara"}, headers=h).json()
    assert one["code"].startswith("bre1.") and one["connector_id"] == found[1]["connector_id"]   # a single system keeps the old form, same connector


def test_pasting_one_code_for_several_systems(ui):
    call, srv, home, port = ui
    raw = {"u": "https://engine.example", "e": [{"c": "c1", "t": "t1", "s": "smepro"}, {"c": "c2", "t": "t2", "s": "imara"}]}
    code = "bre2." + base64.urlsafe_b64encode(json.dumps(raw).encode()).decode().rstrip("=")
    status, body, _ = call("POST", "/api/add", {"code": code})
    assert status == 200 and body["message"].startswith("smePro and Imara connected")
    assert [s["system"] for s in json.loads(A.config_path(home).read_text())["systems"]] == ["smepro", "imara"]
    bad = {**raw, "e": raw["e"] + [{"c": "c3", "t": "t3", "s": "payroll"}]}
    status, _, _ = call("POST", "/api/add", {"code": "bre2." + base64.urlsafe_b64encode(json.dumps(bad).encode()).decode().rstrip("=")})
    assert status == 400 and len(json.loads(A.config_path(home).read_text())["systems"]) == 2      # one unknown system: nothing is added


def test_a_removed_system_leaves_nothing_on_the_computer(client, tmp_path):
    rig = Rig(client, tmp_path, "imara", imara_db)
    cfg = {"base_url": "http://localhost", "systems": [rig.entry], "state_file": str(tmp_path / "gone.db"), "poll_seconds": 0.05, "heartbeat_seconds": 0, "force_pass_seconds": 0}
    box, stop, runners = {}, threading.Event(), []
    import unittest.mock as m
    with m.patch.object(S, "EngineClient", lambda *a, **k: Bridge(client, rig.cid, rig.code["token"])):
        t = threading.Thread(target=S.run_loop, args=(cfg, S.load_profiles(), stop, lambda *a: None),
                             kwargs={"reload": lambda: box.pop("cfg", None), "runners": runners, "config_check_seconds": 0.1})
        t.start()
        for _ in range(100):
            if len(rig.events("sale")) == 2:
                break
            time.sleep(0.2)
        state = S.StateDB(cfg["state_file"])
        assert state.con.execute("SELECT count(*) FROM items WHERE connector_id = ?", (rig.cid,)).fetchone()[0] > 0      # it remembered what it sent
        box["cfg"] = {**cfg, "systems": []}                                  # the owner removed it from this computer
        for _ in range(100):
            if not runners and state.con.execute("SELECT count(*) FROM items WHERE connector_id = ?", (rig.cid,)).fetchone()[0] == 0:
                break
            time.sleep(0.2)
        stop.set()
        t.join(timeout=10)
    assert state.con.execute("SELECT count(*) FROM items WHERE connector_id = ?", (rig.cid,)).fetchone()[0] == 0
    assert state.con.execute("SELECT count(*) FROM meta WHERE connector_id = ?", (rig.cid,)).fetchone()[0] == 0
    state.close()


def test_download_link_is_found_by_itself_and_never_points_at_a_missing_file(monkeypatch):
    import io
    from app.api import agent as ag
    monkeypatch.delenv("AGENT_DOWNLOAD_URL", raising=False)
    monkeypatch.delenv("AGENT_GITHUB_REPO", raising=False)
    monkeypatch.delenv("RENDER_GIT_REPO_SLUG", raising=False)
    ag._DL_CACHE.update(key=None, url=None, until=0.0)
    assert ag.agent_download_url() is None                                    # no repository known: no link
    calls = []

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def published(req, timeout=0):
        calls.append(req.full_url)
        return Resp(json.dumps({"assets": [{"name": "RealityAgent.exe", "state": "uploaded", "browser_download_url": "https://github.com/o/r/releases/download/agent-latest/RealityAgent.exe"}]}).encode())

    monkeypatch.setenv("RENDER_GIT_REPO_SLUG", "o/r")
    monkeypatch.setattr(ag.urllib.request, "urlopen", published)
    assert ag.agent_download_url().endswith("/agent-latest/RealityAgent.exe")
    assert ag.agent_download_url().endswith("/agent-latest/RealityAgent.exe") and len(calls) == 1      # cached

    def missing(req, timeout=0):
        raise OSError("404")

    ag._DL_CACHE.update(key=None, url=None, until=0.0)
    monkeypatch.setattr(ag.urllib.request, "urlopen", missing)
    assert ag.agent_download_url() is None                                    # not built yet: honest, no dead link
    monkeypatch.setenv("AGENT_DOWNLOAD_URL", "https://example.com/x.exe")
    assert ag.agent_download_url() == "https://example.com/x.exe"             # an explicit setting always wins
    monkeypatch.setenv("AGENT_DOWNLOAD_URL", "")
    monkeypatch.setenv("RENDER_GIT_REPO_SLUG", "bad repo/../x")
    ag._DL_CACHE.update(key=None, url=None, until=0.0)
    assert ag.agent_download_url() is None                                    # a malformed repository is never fetched
