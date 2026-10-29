"""
Business Reality Engine - system connectors for the local agent.

Knows where smePro, Imara and Pharmacy ERP keep their local databases, finds them by itself, reads them READ-ONLY,
turns what they hold into the engine's records, and keeps the engine's copy equal to them.

How the pieces fit
  profiles/*.json   everything that is specific to one system: where its database lives, how to recognise it, how its
                    figures are defined. If a system changes, its profile changes; this file does not.
  discovery         the profile's default locations are searched (including other Windows users on the computer),
                    and a location can be set by hand. Several copies: the newest valid one is used and all are listed.
  one read          every pass reads inside ONE read transaction, so a sale and its refund are always seen together.
  change detection  a cheap look at the database files every few seconds; a full, verified pass only when they changed,
                    when the engine asks for a check, or on a safety timer, so nothing waits for the next hour.
  exact copy        each record is fingerprinted; only what is new or edited is sent, deletions are sent as deletions,
                    and every pass ends with control totals (count and sum) the engine compares with what it stored.
                    A mismatch triggers a full resend that also removes what no longer exists.
  honesty           a system that cannot be read says exactly why (not found, wrong file, encrypted and no key, locked,
                    schema changed). It never guesses and never sends partial data as if it were complete.

Standard library only. An encrypted smePro database needs one optional package: pip install sqlcipher3-wheels
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

AGENT_VERSION = "2.1.0"
CHUNK = 500                       # events per request
SWEEP_END = "\U0010ffff"          # sorts after every id: "up to the end of this stream"
SQLITE_MAGIC = b"SQLite format 3\x00"
HEX_KEY = re.compile(r"^[0-9a-fA-F]{64}$")
ZERO_DECIMAL = {"JPY", "KRW", "VND", "UGX", "RWF", "XOF", "XAF", "BIF", "DJF", "GNF", "KMF", "MGA", "PYG", "VUV", "CLP"}
THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "OMR", "TND"}

# What the agent can tell the engine about a system. The engine shows these in plain words.
CONNECTED, NOT_FOUND, NOT_THIS_SYSTEM = "connected", "not_found", "not_this_system"
NEEDS_DRIVER, NEEDS_KEY, LOCKED, UNREADABLE = "needs_driver", "needs_key", "locked", "unreadable"
SCHEMA_CHANGED, NEEDS_CHOICE, NEEDS_CONFIRM, REFUSED = "schema_changed", "needs_choice", "needs_confirmation", "refused"


STATE_WORDS = {CONNECTED: "connected", NOT_FOUND: "not found", NOT_THIS_SYSTEM: "wrong file", NEEDS_DRIVER: "needs the reader installed", NEEDS_KEY: "needs its key file",
               LOCKED: "busy, will retry", UNREADABLE: "cannot be read", SCHEMA_CHANGED: "the system changed", NEEDS_CHOICE: "choose a business",
               NEEDS_CONFIRM: "waiting for confirmation", REFUSED: "connection not accepted", "engine_unreachable": "cannot reach the app"}


class SystemProblem(Exception):
    """Something stops a system from being read. `state` is one of the constants above; `detail` is for the owner."""

    def __init__(self, state: str, detail: str, **extra):
        super().__init__(detail)
        self.state, self.detail, self.extra = state, detail, extra


# ----------------------------------------------------------------------------------------------------------------------
# Profiles
# ----------------------------------------------------------------------------------------------------------------------

def load_profiles(extra_dir: str | os.PathLike | None = None) -> dict[str, dict]:
    """Built-in profiles, then any in `extra_dir` (a profile there replaces the built-in one of the same system, so a
    change in a system can be absorbed by dropping a corrected profile file next to the config, without touching code)."""
    found: dict[str, dict] = {}
    for folder in (Path(__file__).with_name("profiles"), Path(extra_dir) if extra_dir else None):
        if not folder or not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.json")):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                validate_profile(p)
            except (OSError, ValueError) as e:
                print(f"profile {f.name} ignored: {e}", file=sys.stderr)
                continue
            found[p["system"]] = p
    return found


def validate_profile(p: dict) -> None:
    for k in ("system", "label", "profile_version", "discovery", "signature", "streams", "money"):
        if k not in p:
            raise ValueError(f"missing '{k}'")
    seen = set()
    for s in p["streams"]:
        for k in ("key", "entity", "event_type", "table", "sql", "time"):
            if k not in s:
                raise ValueError(f"stream missing '{k}'")
        if s["time"] not in ("instant", "date", "date_or_instant"):
            raise ValueError(f"stream {s['key']}: unknown time rule {s['time']}")
        if s["key"] in seen:
            raise ValueError(f"duplicate stream key {s['key']}")
        seen.add(s["key"])
        if not re.match(r"^\s*(select|with)\b", s["sql"], re.I):
            raise ValueError(f"stream {s['key']}: only SELECT is allowed")


# ----------------------------------------------------------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------------------------------------------------------

def _app_dirs(home: Path, current: bool, env=os.environ) -> dict[str, Path]:
    """Where applications keep their data for `home`'s owner, on this operating system."""
    if sys.platform.startswith("win"):
        appdata = Path(env["APPDATA"]) if current and env.get("APPDATA") else home / "AppData" / "Roaming"
        local = Path(env["LOCALAPPDATA"]) if current and env.get("LOCALAPPDATA") else home / "AppData" / "Local"
    elif sys.platform == "darwin":
        appdata = local = home / "Library" / "Application Support"
    else:
        appdata = local = home / ".local" / "share"
    return {"home": home, "appdata": appdata, "localappdata": local}


def home_directories(home: Path | None = None) -> list[Path]:
    """This user's home first, then the other users' homes on the same computer that can be read (a shop PC where the
    system was installed under another Windows account)."""
    home = home or Path.home()
    homes = [home]
    parent = home.parent
    if parent.name.lower() in ("users", "home"):
        try:
            for d in sorted(parent.iterdir()):
                if d != home and d.is_dir() and d.name.lower() not in ("public", "default", "default user", "all users", "defaultapps", "shared"):
                    homes.append(d)
        except OSError:
            pass
    return homes


def candidate_paths(profile: dict, homes: list[Path] | None = None, env=os.environ) -> list[Path]:
    out, seen = [], set()
    homes = homes or home_directories()
    for i, h in enumerate(homes):
        dirs = _app_dirs(h, current=(i == 0), env=env)
        for template in profile["discovery"]["files"]:
            p = Path(re.sub(r"\{(\w+)\}", lambda m: str(dirs[m.group(1)]), template))
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def locate(profile: dict, override: str | None = None, homes: list[Path] | None = None, env=os.environ) -> dict:
    """Finds the system's database. Returns {"path", "searched", "candidates", "source"}; `path` is None when none exists."""
    if override:
        p = Path(os.path.expanduser(override))
        if p.is_dir():
            p = p / Path(profile["discovery"]["files"][0]).name
        return {"path": p if p.is_file() else None, "searched": [str(p)], "candidates": [str(p)] if p.is_file() else [], "source": "set by hand"}
    searched = candidate_paths(profile, homes, env)
    existing = []
    for p in searched:
        try:
            if p.is_file() and p.stat().st_size > 0:
                existing.append(p)
        except OSError:
            continue
    existing.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return {"path": existing[0] if existing else None, "searched": [str(p) for p in searched],
            "candidates": [str(p) for p in existing], "source": "found automatically"}


def change_token(path: Path) -> tuple:
    """Size and modification time of the database and its write-ahead log: any committed change alters one of them."""
    out = []
    for suffix in ("", "-wal"):
        try:
            st = os.stat(str(path) + suffix)
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


# ----------------------------------------------------------------------------------------------------------------------
# Opening a database, read-only
# ----------------------------------------------------------------------------------------------------------------------

def _sqlcipher():
    try:
        import sqlcipher3  # type: ignore
        return sqlcipher3
    except ImportError:
        return None


def has_sqlcipher() -> bool:
    return _sqlcipher() is not None


def open_readonly(profile: dict, path: Path):
    """A read-only connection. Encrypted databases are opened with the key file the system keeps beside them."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError as e:
        raise SystemProblem(UNREADABLE, f"The file exists but cannot be read ({e.strerror or type(e).__name__}). Check that this user may open it.")
    plain = head == SQLITE_MAGIC
    uri = path.resolve().as_uri() + "?mode=ro"
    enc = profile.get("encryption")
    try:
        if plain:
            con = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        else:
            if not enc:
                raise SystemProblem(NOT_THIS_SYSTEM, "That file is not an SQLite database, so it is not this system's data.")
            lib = _sqlcipher()
            if lib is None:
                raise SystemProblem(NEEDS_DRIVER, f"{profile['label']}'s database is encrypted. Install the reader once with: pip install sqlcipher3-wheels")
            key_path = Path(enc["key_file"].replace("{db}", str(path)))
            try:
                key = key_path.read_text(encoding="utf-8").strip()
            except OSError:
                raise SystemProblem(NEEDS_KEY, f"The key file {key_path.name} that belongs with the database was not found beside it. Without it the data cannot be read.")
            if not HEX_KEY.match(key):
                raise SystemProblem(NEEDS_KEY, f"The key file {key_path.name} is not a valid key.")
            con = lib.connect(uri, uri=True, timeout=5, isolation_level=None)
            con.execute(f"PRAGMA key = \"x'{key}'\"")
        con.execute("PRAGMA query_only = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        try:
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as e:
            con.close()
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                raise SystemProblem(LOCKED, f"{profile['label']} is using its database right now and did not let go in time. This is retried automatically.")
            if enc and not plain:
                raise SystemProblem(NEEDS_KEY, "The key file does not match this database (the database was copied from another computer, or restored without its key).")
            raise SystemProblem(NOT_THIS_SYSTEM, "That file is not a readable database.")
        return con
    except SystemProblem:
        raise
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "locked" in msg or "busy" in msg:
            raise SystemProblem(LOCKED, f"{profile['label']} is using its database right now and did not let go in time. This is retried automatically.")
        raise SystemProblem(UNREADABLE, f"The database could not be opened ({e}).")
    except Exception as e:                               # a driver-level error: never let it look like "no data"
        raise SystemProblem(UNREADABLE, f"The database could not be opened ({type(e).__name__}: {e}).")


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def check_signature(con, profile: dict) -> None:
    missing = [t for t in profile["signature"] if t not in _tables(con)]
    if missing:
        raise SystemProblem(NOT_THIS_SYSTEM, f"A database was found, but it is not {profile['label']}'s (it has no {', '.join(missing)}).")


def version_key(value) -> tuple:
    """'0043_purchase_restock' -> 43, '018-fifo-cost' -> 18, 43 -> 43: comparable across the three systems' styles."""
    if value is None:
        return (0, "")
    s = str(value).strip()
    m = re.match(r"^(\d+)", s)
    return (int(m.group(1)), s) if m else (0, s)


def read_version(con, profile: dict):
    v = profile.get("version")
    if not v:
        return None, None
    try:
        row = con.execute(v["sql"]).fetchone()
    except sqlite3.Error:
        return None, None
    current = row[0] if row else None
    notice = None
    if current is not None and version_key(current)[0] > version_key(v["tested_through"])[0]:
        notice = (f"{profile['label']} has been updated since this connection was last verified (database version {current}, "
                  f"verified up to {v['tested_through']}). Data is still read and checked, but ask for the connection to be re-verified.")
    return current, notice


def list_scopes(con, profile: dict) -> list[dict]:
    sc = profile.get("scope")
    if not sc:
        return []
    return [{"id": str(r[0]), "name": r[1] or "", "currency": r[2] if len(r) > 2 else None} for r in con.execute(sc["sql"])]


def read_currency(con, profile: dict, scope: dict | None) -> str:
    if scope and scope.get("currency"):
        return str(scope["currency"]).upper()
    c = profile.get("currency")
    if c:
        try:
            row = con.execute(c["sql"]).fetchone()
            if row and row[0]:
                return str(row[0]).strip().upper()
        except sqlite3.Error:
            pass
        return c.get("default", "KES")
    return "KES"


def active_streams(con, profile: dict, params: dict) -> list[dict]:
    """The streams that apply to this database. A stream whose table does not exist is skipped only if the profile says it
    is optional (a module the business never switched on); every other statement must compile against the real schema, so
    a renamed table or column is reported as a change, never as 'no data'."""
    have = _tables(con)
    out = []
    for s in profile["streams"]:
        if s["table"] not in have:
            if s.get("optional"):
                out.append({**s, "absent": True})
                continue
            raise SystemProblem(SCHEMA_CHANGED, f"{profile['label']} no longer has the table '{s['table']}' that its {s['entity']} records are read from.",
                                stream=s["key"])
        try:
            con.execute("EXPLAIN " + s["sql"], params).fetchall()
        except sqlite3.Error as e:
            raise SystemProblem(SCHEMA_CHANGED, f"{profile['label']}'s data layout has changed ({e}). The connection needs updating before it can be trusted.",
                                stream=s["key"])
        out.append(s)
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Turning rows into records
# ----------------------------------------------------------------------------------------------------------------------

def places_for(currency: str) -> int:
    c = (currency or "").upper()
    return 0 if c in ZERO_DECIMAL else 3 if c in THREE_DECIMAL else 2


def to_money(raw, unit: str, places, currency: str) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        d = Decimal(str(raw))
    except InvalidOperation:
        return None
    n = places_for(currency) if places == "currency" else int(places)
    if unit == "minor":
        d = d / (Decimal(10) ** n)
    return d.quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)


def parse_utc(text) -> datetime | None:
    if text is None or text == "":
        return None
    s = str(text).strip().replace(" ", "T", 1)
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def parse_day(text) -> date | None:
    if text is None or text == "":
        return None
    try:
        return date.fromisoformat(str(text).strip()[:10])
    except ValueError:
        return None


def make_timestamp(rule: str, day_value, instant_value) -> str | None:
    """A time with a zone ('...Z') is an exact moment; a bare date is read by the engine on the business's own clock, so
    the day the owner recorded is the day the engine reports.
      instant          the stored UTC moment.
      date             the recorded day (falls back to the moment if there is no day).
      date_or_instant  the moment when it falls on the recorded day (so the hour is kept), otherwise the recorded day."""
    inst, day = parse_utc(instant_value), parse_day(day_value)
    if rule == "instant" or (rule == "date" and day is None) or (rule == "date_or_instant" and day is None):
        return inst.strftime("%Y-%m-%dT%H:%M:%SZ") if inst else None
    if rule == "date_or_instant" and inst and inst.date() == day:
        return inst.strftime("%Y-%m-%dT%H:%M:%SZ")
    return day.isoformat() + "T00:00:00" if day else None


def _text(v, limit=200):
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s[:limit] or None


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) and abs(f) < 1e15 else round(f, 4)


def build_event(stream: dict, row: dict, money: dict, currency: str):
    """(event, None) or (None, reason). A row that cannot be read is counted and named, never silently dropped."""
    rid = row.get("id")
    if rid is None or str(rid) == "":
        return None, "no_id"
    ts = make_timestamp(stream["time"], row.get("date"), row.get("instant"))
    if ts is None:
        return None, "no_date"
    amount = to_money(row.get("amount"), money["unit"], money["places"], currency)
    if amount is None:
        return None, "no_amount"
    meta = {}
    for k, v in row.items():
        if k.startswith("m_") and v is not None and v != "":
            meta[k[2:]] = v if isinstance(v, (int, float)) else _text(v)
        elif k.startswith("mm_") and v is not None:
            m = to_money(v, money["unit"], money["places"], currency)
            if m is not None:
                meta[k[3:]] = float(m)
    return {
        "entity": stream["entity"], "entity_id": f"{stream['key']}:{rid}", "event_type": stream["event_type"], "timestamp": ts,
        "amount": float(amount), "quantity": _num(row.get("quantity")), "currency": currency,
        "location": _text(row.get("location")), "customer_id": _text(row.get("customer")),
        "product_id": _text(row.get("product")), "supplier_id": _text(row.get("supplier")), "metadata_json": meta,
    }, None


def fingerprint(event: dict) -> str:
    return hashlib.sha1(json.dumps(event, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def read_stream(con, stream: dict, params: dict, money: dict, currency: str):
    """Yields (entity_id, fingerprint, event) in id order; skipped rows are counted in the returned `skipped` dict."""
    skipped: dict[str, int] = {}
    cur = con.execute(stream["sql"], params)
    cols = [c[0] for c in cur.description]
    for values in cur:
        ev, why = build_event(stream, dict(zip(cols, values)), money, currency)
        if ev is None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        yield ev["entity_id"], fingerprint(ev), ev, skipped


# ----------------------------------------------------------------------------------------------------------------------
# What was already delivered (the agent's own memory, so only differences are sent)
# ----------------------------------------------------------------------------------------------------------------------

class StateDB:
    def __init__(self, path: str):
        self.con = sqlite3.connect(path, timeout=30)
        self.con.execute("PRAGMA journal_mode = WAL")
        self.con.execute("CREATE TABLE IF NOT EXISTS items (connector_id TEXT NOT NULL, stream TEXT NOT NULL, entity_id TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (connector_id, stream, entity_id)) WITHOUT ROWID")
        self.con.execute("CREATE TABLE IF NOT EXISTS meta (connector_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY (connector_id, key)) WITHOUT ROWID")
        self.con.commit()

    def hashes(self, cid: str, stream: str) -> dict[str, str]:
        return dict(self.con.execute("SELECT entity_id, hash FROM items WHERE connector_id = ? AND stream = ?", (cid, stream)))

    def put(self, cid: str, stream: str, items: list[tuple[str, str]]) -> None:
        self.con.executemany("INSERT OR REPLACE INTO items (connector_id, stream, entity_id, hash) VALUES (?, ?, ?, ?)", [(cid, stream, i, h) for i, h in items])
        self.con.commit()

    def drop(self, cid: str, stream: str, ids: list[str]) -> None:
        self.con.executemany("DELETE FROM items WHERE connector_id = ? AND stream = ? AND entity_id = ?", [(cid, stream, i) for i in ids])
        self.con.commit()

    def clear(self, cid: str) -> None:
        self.con.execute("DELETE FROM items WHERE connector_id = ?", (cid,))
        self.con.commit()

    def purge(self, cid: str) -> None:
        """Forgets a connection for good (it was removed), so nothing of it is left on the computer."""
        self.con.execute("DELETE FROM items WHERE connector_id = ?", (cid,))
        self.con.execute("DELETE FROM meta WHERE connector_id = ?", (cid,))
        self.con.commit()

    def get(self, cid: str, key: str, default=None):
        r = self.con.execute("SELECT value FROM meta WHERE connector_id = ? AND key = ?", (cid, key)).fetchone()
        return json.loads(r[0]) if r and r[0] is not None else default

    def set(self, cid: str, key: str, value) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta (connector_id, key, value) VALUES (?, ?, ?)", (cid, key, json.dumps(value)))
        self.con.commit()

    def close(self):
        self.con.close()


# ----------------------------------------------------------------------------------------------------------------------
# Talking to the engine
# ----------------------------------------------------------------------------------------------------------------------

class EngineError(Exception):
    def __init__(self, status: int, detail: str = "", retry_after: float = 0):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail, self.retry_after = status, detail, retry_after


class EngineClient:
    def __init__(self, base_url: str, connector_id: str, token: str, allow_insecure: bool = False, sleep=time.sleep):
        self.base = base_url.rstrip("/")
        self.cid, self.token, self.sleep = connector_id, token, sleep
        if not self.base.startswith("https://") and not (allow_insecure or self.base.startswith(("http://localhost", "http://127.0.0.1"))):
            raise ValueError("refusing to send over plain http; use https (or set allow_insecure for a trusted test)")

    def post(self, what: str, body: dict, timeout: int = 60, attempts: int = 4) -> dict:
        data = json.dumps(body, default=str).encode("utf-8")
        url = f"{self.base}/api/agent/{self.cid}/{what}"
        last: Exception | None = None
        for n in range(attempts):
            req = urllib.request.Request(url, data=data, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.token}", "User-Agent": f"reality-agent/{AGENT_VERSION}"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                try:
                    detail = json.loads(e.read() or b"{}").get("detail", "")
                except ValueError:
                    detail = ""
                if e.code == 429:
                    self.sleep(min(90.0, float(e.headers.get("Retry-After", "5") or 5)))
                    last = EngineError(429, "slow down")
                    continue
                if e.code >= 500:
                    last = EngineError(e.code, str(detail))
                    self.sleep(min(60, 2 ** n))
                    continue
                raise EngineError(e.code, str(detail))
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                last = EngineError(599, f"could not reach the engine ({type(e).__name__})")
                self.sleep(min(60, 2 ** n))
        raise last or EngineError(599, "could not reach the engine")


# ----------------------------------------------------------------------------------------------------------------------
# One system, end to end
# ----------------------------------------------------------------------------------------------------------------------

class SystemRunner:
    """Owns one system: finds it, reads it, keeps the engine equal to it, and reports how it is doing."""

    def __init__(self, cfg: dict, entry: dict, profile: dict, state: StateDB, client, log=print, clock=time.time, homes=None):
        self.cfg, self.entry, self.profile, self.state, self.client, self.log, self.clock, self.homes = cfg, entry, profile, state, client, log, clock, homes
        self.cid = entry["connector_id"]
        self.status: dict = {"state": NOT_FOUND, "detail": "Not checked yet."}
        self.commands: dict = {}
        self.last_token = None
        self.last_pass_at = 0.0
        self.last_heartbeat = 0.0
        self.last_pass_seconds = 0.0
        self.last_pass: dict = {}
        self.force_full = False
        self.ack_check = None
        self.refused_until = 0.0

    # -- finding and opening ----------------------------------------------------------------------------------------
    def _override(self):
        return self.entry.get("path") or self.commands.get("path") or None

    def _open(self):
        """(connection, path, location). Raises SystemProblem with the reason."""
        loc = locate(self.profile, self._override(), self.homes)
        self.status.update(searched=loc["searched"], candidates=loc["candidates"], path_source=loc["source"])
        if loc["path"] is None:
            where = "the location you set" if self._override() else f"the usual places ({len(loc['searched'])} checked)"
            raise SystemProblem(NOT_FOUND, f"{self.profile['label']}'s database was not found in {where}.")
        problem = None
        order = [loc["path"]] + [Path(c) for c in loc["candidates"] if Path(c) != loc["path"]]
        for p in order:                                  # newest first; the next copy is tried if the newest is not this system
            try:
                con = open_readonly(self.profile, p)
                check_signature(con, self.profile)
                return con, p, loc
            except SystemProblem as e:
                problem = problem or e
                if e.state not in (NOT_THIS_SYSTEM, NEEDS_KEY, NEEDS_DRIVER, UNREADABLE):
                    raise
        raise problem

    def _scope(self, con):
        scopes = list_scopes(con, self.profile)
        self.status["scopes"] = [{"id": s["id"], "name": s["name"]} for s in scopes]
        if not self.profile.get("scope"):
            return None
        if not scopes:
            return {"id": None, "empty": True}
        want = self.commands.get("scope_id") or self.entry.get("scope_id")
        if want:
            for s in scopes:
                if s["id"] == str(want):
                    return s
            raise SystemProblem(NEEDS_CHOICE, "The business chosen for this connection is no longer in the database. Choose again.")
        if len(scopes) == 1:
            return scopes[0]
        raise SystemProblem(NEEDS_CHOICE, f"This computer's {self.profile['label']} holds {len(scopes)} businesses. Choose which one this connection reports.")

    # -- one verified pass ----------------------------------------------------------------------------------------
    def read_all(self, con, full: bool):
        """Everything this pass has to say, from one consistent snapshot. Returns a dict, or raises SystemProblem."""
        scope = self._scope(con)
        params = {self.profile["scope"]["param"]: scope["id"]} if scope and scope.get("id") else {}
        if scope and scope.get("empty"):
            streams = []
        else:
            streams = active_streams(con, self.profile, params)
        currency = read_currency(con, self.profile, scope)
        money = self.profile["money"]
        out = {"scope": scope, "currency": currency, "streams": [], "manifest": {}, "skipped": {}, "rows": {}}
        for s in streams:
            known = {} if full else self.state.hashes(self.cid, s["key"])
            seen, upserts, ordered = set(), [], []
            skipped: dict[str, int] = {}
            count, total = 0, Decimal(0)
            if not s.get("absent"):
                for eid, h, ev, skipped in read_stream(con, s, params, money, currency):
                    seen.add(eid)
                    count += 1
                    total += Decimal(str(ev["amount"]))
                    ordered.append((eid, h, ev)) if full else None
                    if not full and known.get(eid) != h:
                        upserts.append((eid, h, ev))
            gone = [i for i in known if i not in seen]
            m = out["manifest"].setdefault(s["entity"], {"count": 0, "sum": Decimal(0)})
            m["count"] += count
            m["sum"] += total
            out["rows"][s["entity"]] = out["rows"].get(s["entity"], 0) + count
            for why, n in skipped.items():
                out["skipped"][f"{s['key']}:{why}"] = n
            out["streams"].append({"stream": s, "upserts": ordered if full else upserts, "gone": gone, "seen": seen, "count": count})
        out["manifest"] = {e: {"count": m["count"], "sum": str(m["sum"])} for e, m in out["manifest"].items()}
        return out

    def run_pass(self, full: bool = False) -> dict:
        """One verified pass. Raises SystemProblem when the system cannot be read, EngineError when the engine refuses."""
        started = self.clock()
        con, path, loc = self._open()
        try:
            token_before = change_token(path)
            version, notice = read_version(con, self.profile)
            con.execute("BEGIN")                                  # one snapshot for everything below
            try:
                data = self.read_all(con, full)
            finally:
                try:
                    con.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
        except sqlite3.OperationalError as e:
            con.close()
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                raise SystemProblem(LOCKED, f"{self.profile['label']} is using its database right now. This is retried automatically.")
            raise SystemProblem(UNREADABLE, f"Reading failed ({e}).")
        except Exception:
            con.close()
            raise
        con.close()
        self.status.update(path=str(path), schema_version=None if version is None else str(version), notice=notice,
                           scope_id=(data["scope"] or {}).get("id"), scope_name=(data["scope"] or {}).get("name"))
        try:
            st = path.stat()
            self.status["db"] = {"size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()}
        except OSError:
            pass
        sent = self._deliver(data, full)
        self.last_token = token_before
        self.last_pass_at = self.clock()
        self.last_pass_seconds = self.last_pass_at - started
        self.last_pass = {"at": datetime.now(timezone.utc).isoformat(), "seconds": round(self.last_pass_seconds, 2), "mode": "full" if full else "delta",
                          "rows": data["rows"], "sent": sent["sent"], "removed": sent["removed"], "skipped": data["skipped"], "verify": sent.get("verify")}
        return {"data": data, "sent": sent}

    # -- delivery ---------------------------------------------------------------------------------------------------
    def _base(self, run_id: str, full: bool) -> dict:
        return {"run_id": run_id, "mode": "full" if full else "delta", "agent": self._agent_info(), "profile": self._profile_info()}

    def _agent_info(self) -> dict:
        import platform
        return {"version": AGENT_VERSION, "host": platform.node()[:80], "os": sys.platform, "python": platform.python_version(), "sqlcipher": has_sqlcipher()}

    def _profile_info(self) -> dict:
        v = self.profile.get("version") or {}
        return {"version": self.profile["profile_version"], "tested_through": v.get("tested_through")}

    def _deliver(self, data: dict, full: bool) -> dict:
        run_id = str(uuid.uuid4())
        sent = removed = 0
        manifest = {"currency": data["currency"], "entities": data["manifest"], "skipped": data["skipped"], "scope": (data["scope"] or {}).get("name")}
        opening = {"first": True, "manifest": manifest} if full else {}     # a full copy announces its totals before removing anything

        def post(extra: dict) -> dict:
            nonlocal opening
            out = self._post_chunk({**self._base(run_id, full), **opening, **extra})
            opening = {}
            return out

        for blk in data["streams"]:
            s, prefix = blk["stream"], blk["stream"]["key"] + ":"
            items = blk["upserts"]
            if full:
                after = prefix
                chunks = [items[i:i + CHUNK] for i in range(0, len(items), CHUNK)] or [[]]
                for n, ch in enumerate(chunks):
                    upto = prefix + SWEEP_END if n == len(chunks) - 1 else ch[-1][0]
                    out = post({"events": [e for _, _, e in ch], "sweep": {"entity": s["entity"], "after": after, "upto": upto}})
                    self.state.put(self.cid, s["key"], [(i, h) for i, h, _ in ch])
                    sent += len(ch)
                    removed += out.get("removed", 0)
                    after = upto
                self.state.drop(self.cid, s["key"], blk["gone"])
            else:
                for i in range(0, len(items), CHUNK):
                    ch = items[i:i + CHUNK]
                    post({"events": [e for _, _, e in ch]})
                    self.state.put(self.cid, s["key"], [(i2, h) for i2, h, _ in ch])
                    sent += len(ch)
        deletes = [] if full else [{"entity": b["stream"]["entity"], "entity_id": i} for b in data["streams"] for i in b["gone"]]
        out = {}
        for i in range(0, max(1, len(deletes)), 1000):
            last = i + 1000 >= len(deletes)
            out = post({"events": [], "deletes": deletes[i:i + 1000], "final": last, **({"manifest": manifest} if last else {})})
            removed += out.get("removed", 0)
        if not full:
            for b in data["streams"]:
                self.state.drop(self.cid, b["stream"]["key"], b["gone"])
        return {"sent": sent, "removed": removed, "verify": out.get("verify"), "action": out.get("action")}

    def _post_chunk(self, body: dict) -> dict:
        try:
            return self.client.post("sync", body, timeout=120)
        except EngineError as e:
            events = body.get("events") or []
            if e.status != 413 or len(events) < 2:
                raise
            half = len(events) // 2                                      # too big for the server: send it in two halves
            head = {k: v for k, v in body.items() if k not in ("manifest", "deletes", "final")}
            tail = {k: v for k, v in body.items() if k != "first"}
            head["events"], tail["events"] = events[:half], events[half:]
            if body.get("sweep"):
                mid = events[half - 1]["entity_id"]
                head["sweep"] = {**body["sweep"], "upto": mid}
                tail["sweep"] = {**body["sweep"], "after": mid}
            self._post_chunk(head)
            return self._post_chunk(tail)

    # -- heartbeat ------------------------------------------------------------------------------------------------
    def heartbeat(self) -> dict:
        body = {"agent": self._agent_info(), "profile": self._profile_info(), "status": self.status, "last_pass": self.last_pass, "ack_check": self.ack_check}
        out = self.client.post("heartbeat", body, timeout=30, attempts=2)
        self.last_heartbeat = self.clock()
        self.commands = out.get("commands") or {}
        return out

    # -- the loop body ------------------------------------------------------------------------------------------------
    def tick(self) -> None:
        """One look: decide whether a pass is needed, do it, and tell the engine. Never raises."""
        now = self.clock()
        if now < self.refused_until:
            return
        poll = float(self.cfg.get("poll_seconds", 20))
        beat = float(self.cfg.get("heartbeat_seconds", 30))
        forced = float(self.cfg.get("force_pass_seconds", 600))
        want_check = self.commands.get("check_now")
        full = bool(self.force_full or self.commands.get("resync"))
        try:
            loc = locate(self.profile, self._override(), self.homes)
            current = change_token(loc["path"]) if loc["path"] else None
            changed = current != self.last_token
            # Never read more than about a fifth of the time: a big database on a slow computer must not be kept busy.
            gap = max(poll, 5 * self.last_pass_seconds)
            waiting_on_person = self.status.get("state") in (SCHEMA_CHANGED, NEEDS_CHOICE, NEEDS_KEY, NEEDS_DRIVER, NEEDS_CONFIRM, NOT_THIS_SYSTEM)
            confirmed = self.status.get("state") == NEEDS_CONFIRM and bool(self.commands.get("confirm"))
            retry_gap = 60 if waiting_on_person else min(poll, 10)
            due = (changed and now - self.last_pass_at >= gap) or now - self.last_pass_at >= forced or full or bool(want_check) or confirmed \
                or (self.status.get("state") != CONNECTED and now - self.last_pass_at >= retry_gap)
            if self.commands.get("paused"):
                due = False
            if due:
                self._attempt(full, want_check)
            elif now - self.last_heartbeat >= beat or want_check:
                self.heartbeat()
        except EngineError as e:
            self._engine_problem(e)
        except Exception as e:                                          # the loop must outlive any surprise
            self.log(f"[{self.profile['label']}] unexpected {type(e).__name__}: {e}")
            self.status.update(state=UNREADABLE, detail=f"Unexpected problem while reading: {type(e).__name__}.")
            self._safe_heartbeat()

    def _attempt(self, full: bool, check) -> None:
        try:
            result = self.run_pass(full)
            self.force_full = False
            action = result["sent"].get("action")
            detail = self._healthy_detail(result)
            self.status.update(state=CONNECTED, detail=detail)
            if action == "resync" and not full:
                self.force_full = True                                  # the engine's totals differ: resend everything once, and remove strays
                self.log(f"[{self.profile['label']}] totals differ from the engine's; resending everything once")
            if check:
                self.ack_check = check
            self._safe_heartbeat()
        except SystemProblem as e:
            self.status.update(state=e.state, detail=e.detail, **e.extra)
            if check:
                self.ack_check = check
            self._safe_heartbeat()
            self.last_pass_at = self.clock()

    def _healthy_detail(self, result: dict) -> str:
        rows = result["data"]["rows"]
        parts = [f"{n:,} {e}{'' if n == 1 else 's'}" for e, n in sorted(rows.items())]
        skipped = sum(result["data"]["skipped"].values())
        return "Reading " + (", ".join(parts) if parts else "no records yet") + (f"; {skipped:,} rows could not be read and were left out" if skipped else "") + "."

    def _safe_heartbeat(self) -> None:
        try:
            self.heartbeat()
        except EngineError as e:
            self._engine_problem(e)

    def _engine_problem(self, e: EngineError) -> None:
        if e.status in (401, 403, 404):
            self.log(f"[{self.profile['label']}] the engine no longer accepts this connection ({e.status}). It was disconnected or its code was replaced in the app.")
            self.status.update(state=REFUSED, detail="The engine no longer accepts this connection. Connect it again in the app and run setup with the new code.")
            self.refused_until = self.clock() + 300
        elif e.status == 409 and "changed a lot" in (e.detail or "").lower():
            self.status.update(state=NEEDS_CONFIRM, detail=e.detail)
            self.last_pass_at = self.clock()
            self._safe_heartbeat()
        elif e.status == 409:
            self.refused_until = self.clock() + 60
        else:
            self.log(f"[{self.profile['label']}] engine not reachable or busy: {e}")
            if e.status >= 500:
                self.status.update(state="engine_unreachable", detail="Cannot reach the app. Check this computer's internet connection; it keeps trying by itself.")
            else:
                self.status.update(state=UNREADABLE, detail=f"The app did not accept the data ({e.detail or e.status}). The agent may need updating.")


# ----------------------------------------------------------------------------------------------------------------------
# Config, codes, and the loop that runs every system
# ----------------------------------------------------------------------------------------------------------------------

def decode_codes(code: str) -> list[dict]:
    """A connection code from the app. 'bre1.' + base64url(JSON {u: engine address, c: connector id, t: token, s: system}) for one
    system, 'bre2.' + base64url(JSON {u, e: [{c, t, s}, ...]}) for several at once. Always returns one dict per system."""
    code = code.strip()
    if not code.startswith(("bre1.", "bre2.")):
        raise ValueError("That is not a connection code from the app (it should start with bre1. or bre2.)")
    try:
        d = json.loads(base64.urlsafe_b64decode(code[5:] + "=" * (-len(code[5:]) % 4)))
        entries = d["e"] if code.startswith("bre2.") else [d]
        out = [{"base_url": d["u"], "connector_id": e["c"], "token": e["t"], "system": e["s"]} for e in entries]
        assert out and all(isinstance(v, str) and v for o in out for v in o.values())
        return out
    except Exception:
        raise ValueError("The connection code is damaged. Copy it again from the app, all of it.")


def add_system(cfg: dict, code: str, profiles: dict) -> dict:
    """Adds every system in the code, or none: a code with one problem changes nothing."""
    found = decode_codes(code)
    for d in found:
        if d["system"] not in profiles:
            raise ValueError(f"This agent does not know the system '{d['system']}'. Update the agent.")
        if cfg.get("base_url") and cfg["base_url"].rstrip("/") != d["base_url"].rstrip("/"):
            raise ValueError(f"This code is for {d['base_url']}, but this agent already reports to {cfg['base_url']}.")
    if len({d["base_url"].rstrip("/") for d in found}) > 1:
        raise ValueError("The connection code is damaged. Copy it again from the app, all of it.")
    cfg["base_url"] = found[0]["base_url"]
    added = {d["system"] for d in found}
    systems = [s for s in cfg.get("systems", []) if s["system"] not in added]
    systems += [{"system": d["system"], "connector_id": d["connector_id"], "token": d["token"]} for d in found]
    cfg["systems"] = systems
    return cfg


def check_report(cfg: dict, profiles: dict, homes=None) -> list[dict]:
    """What the agent sees on this computer, without sending anything: used by --check and by setup."""
    out = []
    for entry in cfg.get("systems", []):
        p = profiles.get(entry["system"])
        if p is None:
            out.append({"system": entry["system"], "state": SCHEMA_CHANGED, "detail": "Unknown system."})
            continue
        r = {"system": p["label"], "state": CONNECTED, "detail": ""}
        try:
            loc = locate(p, entry.get("path"), homes)
            r.update(searched=loc["searched"], candidates=loc["candidates"])
            if not loc["path"]:
                raise SystemProblem(NOT_FOUND, f"{p['label']}'s database was not found.")
            con = open_readonly(p, loc["path"])
            try:
                check_signature(con, p)
                version, notice = read_version(con, p)
                scopes = list_scopes(con, p)
                scope = scopes[0] if len(scopes) == 1 else None
                if p.get("scope") and len(scopes) > 1 and not entry.get("scope_id"):
                    raise SystemProblem(NEEDS_CHOICE, f"{len(scopes)} businesses in this database: " + ", ".join(s["name"] for s in scopes))
                if entry.get("scope_id"):
                    scope = next((s for s in scopes if s["id"] == str(entry["scope_id"])), scope)
                params = {p["scope"]["param"]: scope["id"]} if scope else {}
                currency = read_currency(con, p, scope)
                streams = active_streams(con, p, params) if (scope or not p.get("scope")) else []
                counts = {}
                for s in streams:
                    if s.get("absent"):
                        continue
                    n = sum(1 for _ in read_stream(con, s, params, p["money"], currency))
                    counts[s["entity"]] = counts.get(s["entity"], 0) + n
                r.update(path=str(loc["path"]), schema_version=version, notice=notice, currency=currency, counts=counts,
                         detail="Readable. " + ", ".join(f"{n:,} {e}s" for e, n in sorted(counts.items())))
            finally:
                con.close()
        except SystemProblem as e:
            r.update(state=e.state, detail=e.detail)
        out.append(r)
    return out


def run_loop(cfg: dict, profiles: dict, stop: threading.Event, log=print, once: bool = False, reload=None, runners=None, config_check_seconds: float = 5.0) -> int:
    """One thread per system, so a long first copy of one never delays another's heartbeat.
    `reload()` returns the latest config (or None when unchanged): a system added or removed while the agent runs
    starts or stops by itself, so connecting the second and third system needs no restart. `runners` lists the live
    per-system runners for whoever wants to show their status."""
    runners = runners if runners is not None else []
    live: dict[str, tuple[threading.Thread, threading.Event, dict]] = {}
    removed: set[str] = set()                                           # connections taken out of the config: their local memory is wiped when they stop
    state_path = cfg.get("state_file", "agent_state.db")

    def work(cur: dict, entry: dict, mine: threading.Event):
        profile = profiles[entry["system"]]
        client = EngineClient(cur["base_url"], entry["connector_id"], entry["token"], cur.get("allow_insecure", False))
        state = StateDB(state_path)
        runner = SystemRunner(cur, entry, profile, state, client, log)
        runners.append(runner)
        try:
            while not stop.is_set() and not mine.is_set():
                runner.tick()
                if once and runner.last_heartbeat:
                    break
                (mine.wait if not once else stop.wait)(float(cur.get("poll_seconds", 20)) if not once else 1)
        finally:
            if entry["connector_id"] in removed:
                removed.discard(entry["connector_id"])
                state.purge(entry["connector_id"])
            state.close()
            if runner in runners:
                runners.remove(runner)

    def sync_to(cur: dict):
        wanted = {e["connector_id"]: e for e in cur.get("systems", []) if e["system"] in profiles}
        for cid in [c for c, (_, _, e) in live.items() if c not in wanted or wanted[c] != e]:
            if cid not in wanted:
                removed.add(cid)
            live.pop(cid)[1].set()                                      # removed or changed (new code, new location): stop it, restart below
        for cid, entry in wanted.items():
            if cid not in live:
                mine = threading.Event()
                t = threading.Thread(target=work, args=(cur, entry, mine), name=entry["system"], daemon=True)
                live[cid] = (t, mine, dict(entry))
                t.start()
        for e in cur.get("systems", []):
            if e["system"] not in profiles:
                log(f"unknown system {e['system']!r}; skipped")

    sync_to(cfg)
    last_check = time.monotonic()
    try:
        while not stop.is_set() and (reload is not None or any(t.is_alive() for t, _, _ in live.values())):
            stop.wait(0.5)
            if reload is not None and time.monotonic() - last_check >= config_check_seconds:
                last_check = time.monotonic()
                try:
                    fresh = reload()
                except Exception as e:                                  # a half-written config must not stop the agent
                    log(f"config not reloaded: {type(e).__name__}")
                    fresh = None
                if fresh is not None:
                    cfg = fresh
                    sync_to(cfg)
            if once and not any(t.is_alive() for t, _, _ in live.values()):
                break
    except KeyboardInterrupt:
        stop.set()
    for _, mine, _ in live.values():
        mine.set()
    bad = [r for r in runners if r.status.get("state") != CONNECTED]
    return 1 if once and bad else 0
