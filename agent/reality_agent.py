#!/usr/bin/env python3
"""
Business Reality Engine - local agent (Section 8-I, and 8-J/K through the `command` source).

Runs on a machine INSIDE the business's network, reads local data and pushes it OUT over HTTPS to the engine.
It never listens on a port, so nothing needs to be opened on the business's firewall. Standard library only
(Python 3.9+); `pyodbc` is optional and only needed for the `odbc` source.

Local systems (smePro, Imara, Pharmacy ERP): python reality_agent.py add <code> | check | run   (see README)

Usage:   python reality_agent.py config.json            # run forever, every interval_seconds
         python reality_agent.py config.json --once     # one pass (use with Task Scheduler / cron)
         python reality_agent.py config.json --dry-run  # read and print counts, send nothing

config.json
{
  "base_url": "https://engine.example.com",
  "connector_id": "<connector id>",           # a connector of type local_agent (created in the app; shows the token once)
  "token": "<token shown once at creation>",
  "interval_seconds": 300, "batch_size": 200, "state_file": "agent_state.json",
  "source": ONE OF
    {"type": "csv_folder", "path": "C:/exports", "pattern": "*.csv"}      # new/changed files only (by content hash)
    {"type": "sqlite", "path": "C:/data/pos.db", "query": "SELECT ... WHERE id > :since", "incremental_column": "id"}
    {"type": "odbc", "connection_string": "DSN=pos;UID=ro;PWD=...", "query": "SELECT ..."}
    {"type": "command", "argv": ["python", "export_from_old_app.py"], "timeout_seconds": 120}
}

The `command` source runs YOUR export script (no shell) and reads one JSON object per line from its output. This is how a
legacy program with no export is covered: a script you control (including one that drives the program's own screen with
your authorization, or reads values a person typed in) prints rows; the agent only transports them.

Rows are sent in the source's own column names; the connector's mapping (set in the app) turns them into events. Resending is
safe: events with an entity_id are de-duplicated by the server.
"""
import csv
import glob
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal

MAX_COMMAND_OUTPUT = 50 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


# ---------------- sources: each returns (rows, apply_state) ; apply_state(state) runs ONLY after a successful send ----------------

def read_csv_folder(src, state):
    seen = set(state.get("files", []))
    rows, new_hashes = [], []
    for p in sorted(glob.glob(os.path.join(src["path"], src.get("pattern", "*.csv")))):
        if not os.path.isfile(p) or os.path.getsize(p) > MAX_FILE_BYTES:
            continue
        with open(p, "rb") as f:
            raw = f.read()
        h = hashlib.sha256(raw).hexdigest()
        if h in seen:
            continue
        text = raw.decode("utf-8-sig", errors="replace").splitlines()
        rows.extend(dict(r) for r in csv.DictReader(text))
        new_hashes.append(h)

    def apply(st):
        st["files"] = (st.get("files", []) + new_hashes)[-5000:]
    return rows, apply


def read_sqlite(src, state):
    since = state.get("since", src.get("initial_since", 0))
    con = sqlite3.connect(f"file:{src['path']}?mode=ro", uri=True)
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(src["query"], {"since": since} if ":since" in src["query"] else {})
        rows = [dict(r) for r in cur.fetchall()]
    finally:
        con.close()
    col = src.get("incremental_column")
    top = max((r[col] for r in rows if r.get(col) is not None), default=None) if col else None

    def apply(st):
        if top is not None:
            st["since"] = top
    return rows, apply


def read_odbc(src, state):
    import pyodbc  # optional dependency
    con = pyodbc.connect(src["connection_string"], timeout=15, readonly=True)
    try:
        cur = con.cursor()
        cur.execute(src["query"])
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()
    return rows, (lambda st: None)


def read_command(src, state):
    argv = src["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("command.argv must be a non-empty list of strings")
    proc = subprocess.run(argv, capture_output=True, timeout=src.get("timeout_seconds", 120), shell=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command exited with {proc.returncode}: {proc.stderr[-300:].decode(errors='replace')}")
    if len(proc.stdout) > MAX_COMMAND_OUTPUT:
        raise RuntimeError("command output too large")
    rows = []
    for i, line in enumerate(proc.stdout.decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise RuntimeError(f"command output line {i} is not JSON")
        if not isinstance(obj, dict):
            raise RuntimeError(f"command output line {i} is not a JSON object")
        rows.append(obj)
    return rows, (lambda st: None)


READERS = {"csv_folder": read_csv_folder, "sqlite": read_sqlite, "odbc": read_odbc, "command": read_command}


# ---------------- transport ----------------

def http_post(url, body, timeout=60, allow_insecure=False):
    if not url.startswith("https://") and not (allow_insecure or url.startswith("http://localhost") or url.startswith("http://127.0.0.1")):
        raise ValueError("refusing to send over plain http; use https (or set allow_insecure for a trusted test)")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json", "User-Agent": "reality-agent/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def send_rows(cfg, rows, post=None, sleep=time.sleep):
    """Send in batches with retry/backoff. Returns totals; raises if a batch cannot be delivered (state is then NOT advanced)."""
    post = post or (lambda url, body: http_post(url, body, allow_insecure=cfg.get("allow_insecure", False)))
    url = f"{cfg['base_url'].rstrip('/')}/api/webhooks/{cfg['connector_id']}/{cfg['token']}/batch"
    size = max(1, min(int(cfg.get("batch_size", 200)), 500))
    totals = {"sent": 0, "accepted": 0, "duplicates": 0, "rejected": 0}
    for i in range(0, len(rows), size):
        chunk = rows[i:i + size]
        body = json.dumps({"events": chunk}, default=_json_default).encode()
        for attempt in range(5):
            try:
                status, out = post(url, body)
            except (urllib.error.URLError, OSError, TimeoutError):
                status, out = 599, {}
            if status == 200:
                totals["sent"] += len(chunk)
                totals["accepted"] += out.get("accepted", 0)
                totals["duplicates"] += out.get("duplicates", 0)
                totals["rejected"] += len(out.get("rejected", []))
                break
            if status in (400, 401, 403, 404, 409, 413, 422):          # a retry cannot fix these
                raise RuntimeError(f"server refused the batch (HTTP {status}): {out.get('detail', '')}")
            sleep(min(60, 2 ** attempt))
        else:
            raise RuntimeError("could not deliver a batch after 5 attempts")
    return totals


def run_once(cfg, post=None, dry_run=False, sleep=time.sleep):
    state_file = cfg.get("state_file", "agent_state.json")
    state = load_state(state_file)
    src = cfg["source"]
    reader = READERS.get(src.get("type"))
    if reader is None:
        raise ValueError(f"unknown source type {src.get('type')!r}; use one of {sorted(READERS)}")
    rows, apply_state = reader(src, state)
    if dry_run:
        return {"read": len(rows), "dry_run": True}
    if not rows:
        return {"read": 0, "sent": 0}
    totals = send_rows(cfg, rows, post=post, sleep=sleep)
    apply_state(state)                         # only after everything was delivered
    save_state(state_file, state)
    return {"read": len(rows), **totals}


def default_config_path():
    return os.path.join(os.path.expanduser("~"), ".reality-agent", "config.json")


def _save_config(path, cfg):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(tmp, 0o600)                                   # it holds the connection codes' tokens
    except OSError:
        pass
    os.replace(tmp, path)


def systems_main(argv):
    """smePro / Imara / Pharmacy ERP on this computer (see systems.py).
         add <code>            connect a system with the code the app shows (repeat for each system; any combination)
         check                 what this computer can see, per system, without sending anything
         run [--once]          keep every connected system in sync (this is what runs all day)
         path <system> <file>  tell the agent where a system's database is (clear with '')
       --config <file>        default: ~/.reality-agent/config.json"""
    import argparse
    import threading
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import systems as S
    ap = argparse.ArgumentParser(prog="reality_agent.py", description=systems_main.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["add", "check", "run", "path"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--config", default=default_config_path())
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv[1:])
    try:
        with open(a.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except OSError:
        cfg = {}
    except ValueError:
        print(f"{a.config} is damaged; fix or delete it.", file=sys.stderr)
        return 2
    cfg.setdefault("state_file", os.path.join(os.path.dirname(os.path.abspath(a.config)), "state.db"))
    profiles = S.load_profiles(os.path.join(os.path.dirname(os.path.abspath(a.config)), "profiles.d"))
    if a.command == "add":
        if len(a.args) != 1:
            print("usage: reality_agent.py add <code from the app>", file=sys.stderr)
            return 2
        try:
            cfg = S.add_system(cfg, a.args[0], profiles)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        _save_config(a.config, cfg)
        print("Connected. What this computer sees:")
    if a.command == "path":
        if len(a.args) != 2 or not any(x["system"] == a.args[0] for x in cfg.get("systems", [])):
            print("usage: reality_agent.py path <smepro|imara|pharmacy> <file or folder, or '' to find it automatically>", file=sys.stderr)
            return 2
        for x in cfg["systems"]:
            if x["system"] == a.args[0]:
                x.pop("path", None) if not a.args[1] else x.update(path=a.args[1])
        _save_config(a.config, cfg)
    if a.command in ("add", "check", "path"):
        if not cfg.get("systems"):
            print("No system is connected yet. In the app open Data, choose the system, and run: reality_agent.py add <code>")
            return 1
        report = S.check_report(cfg, profiles)
        for r in report:
            print(f"  {r['system']}: {S.STATE_WORDS.get(r['state'], r['state'])}" + (f" - {r['detail']}" if r["detail"] else ""))
            if r["state"] != S.CONNECTED and r.get("searched"):
                print("     looked in: " + "; ".join(r["searched"]))
            if r.get("candidates") and len(r["candidates"]) > 1:
                print("     several copies found, using the newest: " + "; ".join(r["candidates"]))
        return 0 if all(r["state"] == S.CONNECTED for r in report) else 1
    if not cfg.get("systems"):
        print("No system is connected yet. Run: reality_agent.py add <code from the app>", file=sys.stderr)
        return 2
    stop = threading.Event()
    log = lambda *m: print(datetime.now().isoformat(timespec="seconds"), *m, flush=True)
    log("reality agent", S.AGENT_VERSION, "watching:", ", ".join(x["system"] for x in cfg["systems"]))
    return S.run_loop(cfg, profiles, stop, log, once=a.once)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        print(systems_main.__doc__)
        return 2
    if argv[1] in ("add", "check", "run", "path"):
        return systems_main(argv)
    with open(argv[1], "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if "systems" in cfg and "source" not in cfg:
        return systems_main([argv[0], "run", "--config", argv[1]] + (["--once"] if "--once" in argv else []))
    once, dry = "--once" in argv, "--dry-run" in argv
    interval = int(cfg.get("interval_seconds", 300))
    while True:
        try:
            print(datetime.now().isoformat(timespec="seconds"), run_once(cfg, dry_run=dry), flush=True)
        except Exception as e:                                  # keep running; the next pass retries
            print(datetime.now().isoformat(timespec="seconds"), "ERROR", type(e).__name__, str(e)[:300], file=sys.stderr, flush=True)
            if once:
                return 1
        if once:
            return 0
        time.sleep(max(30, interval))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
