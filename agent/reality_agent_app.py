#!/usr/bin/env python3
"""
Business Reality Engine agent - the one-file version for ordinary users.

Double-click it. A page opens in the browser, the owner pastes the code from the app, and it says whether the system was
found and synced. It then keeps running quietly in the background, starts with the computer, and picks up a second or
third system added later without being restarted. There is nothing to install and nothing to type.

The page is served only to this computer (127.0.0.1), needs a key that lives in the owner's own folder, and refuses
requests from other websites, so a web page cannot use it to connect the agent to someone else's account.

    RealityAgent.exe                open the page (and start if it is not running)
    RealityAgent.exe --background   start quietly (what runs at start-up)
    RealityAgent.exe --remove-startup
"""
import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reality_agent as R  # noqa: E402
import systems as S  # noqa: E402

PORT = 47821
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "RealityAgent"
MAX_LOG_BYTES = 2 * 1024 * 1024


def home_dir() -> Path:
    return Path(os.environ.get("REALITY_AGENT_HOME") or (Path.home() / ".reality-agent"))


def config_path(home: Path) -> Path:
    return home / "config.json"


def read_config(home: Path) -> dict:
    try:
        cfg = json.loads(config_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("state_file", str(home / "state.db"))
    return cfg


def make_logger(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    path = home / "agent.log"
    lock = threading.Lock()

    def log(*parts):
        line = f"{datetime.now().isoformat(timespec='seconds')} {' '.join(str(p) for p in parts)}\n"
        with lock:
            try:
                if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
                    path.replace(home / "agent.log.old")
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass
    return log


def profiles_for(home: Path) -> dict:
    return S.load_profiles(home / "profiles.d")


# ---- start with the computer ------------------------------------------------------------------------------------------

def startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}" --background'


def set_startup(enable: bool) -> bool:
    """Registers (or removes) the start-with-Windows entry for this user. No administrator rights are needed. False elsewhere."""
    if not sys.platform.startswith("win"):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---- the page --------------------------------------------------------------------------------------------------------

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Business Reality Engine agent</title>
<style>
body{font:16px/1.5 system-ui,Segoe UI,sans-serif;margin:0;background:#f5f6f4;color:#1c2321}
main{max-width:640px;margin:0 auto;padding:28px 20px}
h1{font-size:22px;margin:0 0 4px} p.sub{margin:0 0 22px;color:#566}
.card{background:#fff;border:1px solid #d9ddd9;border-radius:12px;padding:16px 18px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;background:#8a8f98}
.ok{background:#1b8a5a}.wait{background:#d4a017}.bad{background:#c0392b}
small{color:#566} input[type=text]{width:100%;box-sizing:border-box;padding:11px;border:1px solid #b9c0bb;border-radius:8px;font-size:15px}
button{font-size:15px;padding:10px 18px;border-radius:8px;border:1px solid #b9c0bb;background:#fff;cursor:pointer}
button.main{background:#1b5a54;color:#fff;border-color:#1b5a54}
#msg{margin:10px 0;min-height:1.4em}.err{color:#c0392b}.good{color:#1b8a5a}
</style></head><body><main>
<h1>Business Reality Engine</h1>
<p class="sub">This small program lets the app see your business systems on this computer. You can close this page: it keeps working in the background and starts with the computer.</p>
<div id="systems"></div>
<div class="card"><b>Connect a system</b>
<p><small>In the app, open <b>Data &rarr; Your systems</b>, tick the systems on this computer, copy the code, and paste it here. One code connects them all.</small></p>
<form id="f"><input type="text" id="code" placeholder="Paste the code here" autocomplete="off" spellcheck="false">
<p><button class="main" id="go">Connect</button></p></form><div id="msg" role="status"></div></div>
<script>
const $=s=>document.querySelector(s);
const esc=t=>String(t??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function api(p,b){const r=await fetch(p,{method:b?"POST":"GET",headers:{"Content-Type":"application/json"},body:b?JSON.stringify(b):undefined,credentials:"same-origin"});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||"Something went wrong");return j}
function tone(s){return s==="connected"?"ok":["needs_choice","needs_confirmation","locked","starting","engine_unreachable"].includes(s)?"wait":"bad"}
async function draw(){try{const j=await api("/api/status");
$("#systems").innerHTML=j.systems.map(s=>`<div class="card"><div class="row"><div><b>${esc(s.label)}</b><br><small>${esc(s.detail||"")}</small>${s.path?`<br><small>${esc(s.path)}</small>`:""}</div>
<div><span class="dot ${tone(s.state)}"></span>${esc(s.headline)}</div></div>
${s.state==="not_found"||s.state==="not_this_system"?`<p><small>Not in the usual place? Paste the full path of its database file:</small><br><input type="text" id="p_${esc(s.system)}" placeholder="C:\\\\...\\\\database file"> <button onclick="setPath('${esc(s.system)}')">Use this file</button></p>`:""}
<p><button onclick="rm('${esc(s.system)}')"><small>Remove from this computer</small></button></p></div>`).join("")}catch(e){}}
async function setPath(sys){try{await api("/api/path",{system:sys,path:$("#p_"+sys).value.trim()});$("#msg").className="good";$("#msg").textContent="Saved. Checking it now."}catch(e){$("#msg").className="err";$("#msg").textContent=e.message}}
async function rm(sys){if(!confirm("Stop reading this system on this computer? Its data already in the app stays until you disconnect it there."))return;try{await api("/api/remove",{system:sys});draw()}catch(e){$("#msg").className="err";$("#msg").textContent=e.message}}
$("#f").onsubmit=async e=>{e.preventDefault();$("#go").disabled=true;$("#msg").className="";$("#msg").textContent="Connecting…";
try{const j=await api("/api/add",{code:$("#code").value.trim()});$("#code").value="";$("#msg").className="good";$("#msg").textContent=j.message;draw()}catch(e){$("#msg").className="err";$("#msg").textContent=e.message}$("#go").disabled=false};
draw();setInterval(draw,3000);
</script></main></body></html>"""


def build_server(home: Path, runners: list, port: int = PORT, log=print, key: str | None = None):
    """The local page and its small API. `runners` is the live list from run_loop."""
    key = key or secrets.token_urlsafe(24)
    home.mkdir(parents=True, exist_ok=True)
    key_file = home / "ui.key"
    key_file.write_text(key, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    write_lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "RealityAgent"

        def log_message(self, *a):
            pass

        def _port(self):
            return self.server.server_address[1]

        def _send(self, code, body, ctype="application/json", extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj))

        def _host_ok(self):
            return (self.headers.get("Host") or "") in (f"127.0.0.1:{self._port()}", f"localhost:{self._port()}")

        def _cookie_ok(self):
            for part in (self.headers.get("Cookie") or "").split(";"):
                name, _, val = part.strip().partition("=")
                if name == "bre_k" and secrets.compare_digest(val, key):
                    return True
            return False

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "Not allowed"})
            url = urlparse(self.path)
            given = (parse_qs(url.query).get("k") or [""])[0]
            if given and secrets.compare_digest(given, key):          # opened from the agent itself: remember the key, then drop it from the address
                return self._send(302, b"", "text/plain", extra={"Location": "/", "Set-Cookie": f"bre_k={key}; HttpOnly; SameSite=Strict; Path=/"})
            if not self._cookie_ok():
                return self._send(403, "Open this page from the agent (double-click it again).", "text/plain")
            if url.path == "/api/status":
                return self._json(200, {"systems": status_list(home, runners)})
            if url.path == "/":
                return self._send(200, PAGE, "text/html")
            self._json(404, {"error": "Not found"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._host_ok() or not self._cookie_ok() or (origin and origin not in (f"http://127.0.0.1:{self._port()}", f"http://localhost:{self._port()}")):
                return self._json(403, {"error": "Not allowed"})
            try:
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(min(size, 65536)) or b"{}")
                assert isinstance(body, dict)
            except (ValueError, AssertionError):
                return self._json(400, {"error": "That request was not understood."})
            path = urlparse(self.path).path
            with write_lock:
                cfg = read_config(home)
                profiles = profiles_for(home)
                if path == "/api/add":
                    try:
                        cfg = S.add_system(cfg, str(body.get("code", "")), profiles)
                    except ValueError as e:
                        return self._json(400, {"error": str(e)})
                    R._save_config(str(config_path(home)), cfg)
                    set_startup(True)
                    labels = [profiles[d["system"]]["label"] for d in S.decode_codes(str(body["code"]))]
                    label = " and ".join([", ".join(labels[:-1]), labels[-1]] if len(labels) > 1 else labels)
                    log("connected", label)
                    return self._json(200, {"message": f"{label} connected. Looking for {'their' if len(labels) > 1 else 'its'} data…"})
                known = {x["system"] for x in cfg.get("systems", [])}
                system = str(body.get("system", ""))
                if system not in known:
                    return self._json(404, {"error": "That system is not connected here."})
                if path == "/api/path":
                    p = str(body.get("path", "")).strip().strip('"')
                    for x in cfg["systems"]:
                        if x["system"] == system:
                            x.pop("path", None) if not p else x.update(path=p)
                elif path == "/api/remove":
                    cfg["systems"] = [x for x in cfg["systems"] if x["system"] != system]
                else:
                    return self._json(404, {"error": "Not found"})
                R._save_config(str(config_path(home)), cfg)
                return self._json(200, {"ok": True})

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = False          # a second copy must fail to start: that is how it knows one is already running

    srv = Server(("127.0.0.1", port), Handler)
    srv.key = key
    return srv


def status_list(home: Path, runners: list) -> list[dict]:
    profiles = profiles_for(home)
    live = {r.entry["system"]: r for r in list(runners)}
    out = []
    for e in read_config(home).get("systems", []):
        p = profiles.get(e["system"])
        if p is None:
            continue
        r = live.get(e["system"])
        st = (r.status if r else {}) or {}
        state = st.get("state") if r and (r.last_heartbeat or st.get("state") == "engine_unreachable") else "starting"
        out.append({"system": e["system"], "label": p["label"], "state": state,
                    "headline": "Connected and synced" if state == S.CONNECTED else ("Starting…" if state == "starting" else S.STATE_WORDS.get(state, state).capitalize()),
                    "detail": st.get("detail") if state != "starting" else "Looking for its data…", "path": st.get("path") if state == S.CONNECTED else None})
    return out


def run_agent(home: Path, stop: threading.Event, log, runners: list) -> None:
    mtime = {"v": None}

    def reload():
        try:
            m = config_path(home).stat().st_mtime_ns
        except OSError:
            m = None
        if m == mtime["v"]:
            return None
        mtime["v"] = m
        return read_config(home)

    cfg = read_config(home)
    mtime["v"] = config_path(home).stat().st_mtime_ns if config_path(home).exists() else None
    S.run_loop(cfg, profiles_for(home), stop, log, reload=reload, runners=runners)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    home = home_dir()
    if "--remove-startup" in argv:
        print("Removed from start-up." if set_startup(False) else "Nothing to remove.")
        return 0
    log = make_logger(home)
    background = "--background" in argv
    runners: list = []
    try:
        srv = build_server(home, runners, PORT, log)
    except OSError:                                              # already running: just show its page
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/?k={(home / 'ui.key').read_text(encoding='utf-8').strip()}")
        except OSError:
            pass
        return 0
    stop = threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True, name="page").start()
    threading.Thread(target=run_agent, args=(home, stop, log, runners), daemon=True, name="agent").start()
    log("agent", S.AGENT_VERSION, "started")
    if set_startup(True) is False and sys.platform.startswith("win"):
        log("could not register to start with the computer")
    if not background or not read_config(home).get("systems"):
        webbrowser.open(f"http://127.0.0.1:{PORT}/?k={srv.key}")
    try:
        while not stop.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
