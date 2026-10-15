"""
Web link / API connector (Section 8-A): pulls a CSV, Excel or JSON document from an https URL on a schedule.
Covers, with the SAME column-mapping path every other connector uses:
  - Google Sheets (any sheet shared as "anyone with the link can view": the share link is converted to a CSV export)
  - OneDrive/Dropbox/GitHub/any direct download link to a CSV / XLSX / JSON file
  - REST APIs that return JSON (WooCommerce, Shopify and similar), with an API key or basic-auth login and simple paging

Safety: https only (http only when private targets are explicitly allowed), the target host is checked on every redirect hop
(so a public URL cannot bounce us onto an internal address), response size is capped while streaming, and credentials are
encrypted at rest and never echoed back.
"""
import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from fastapi import HTTPException

from app.connectors.file_connector import MAX_FILE_SIZE_BYTES, parse_file_to_dataframe
from app.core.netguard import check_host_allowed
from app.core.secrets import decrypt_config_secrets

TIMEOUT = httpx.Timeout(25.0, connect=10.0)
MAX_REDIRECTS = 4
MAX_PAGES = 25
AUTH_TYPES = {"none", "bearer", "header", "basic"}

_GSHEET = re.compile(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)")


def normalize_url(url: str) -> str:
    """Turn friendly share links into direct-download links."""
    url = (url or "").strip()
    m = _GSHEET.match(url)
    if m and "/export" not in url and "/pub" not in url:
        gid = re.search(r"[#&?]gid=(\d+)", url)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    p = urlparse(url)
    if p.netloc.endswith("dropbox.com"):
        q = dict(parse_qsl(p.query)); q.pop("dl", None); q["dl"] = "1"
        return urlunparse(p._replace(query=urlencode(q)))
    return url


def validate_url(url: str) -> str:
    url = normalize_url(url)
    p = urlparse(url)
    if p.scheme not in ("https", "http") or not p.hostname:
        raise HTTPException(400, "Enter a full web address starting with https://")
    if p.scheme == "http":
        import os
        if os.environ.get("ALLOW_PRIVATE_WEBHOOK_TARGETS", "").lower() != "true":
            raise HTTPException(400, "Use an https:// address")
    if p.username or p.password:
        raise HTTPException(400, "Don't put a login in the address; use the authentication fields")
    check_host_allowed(p.hostname)
    return url


def _auth(cfg: dict) -> tuple[dict, Optional[tuple]]:
    kind = cfg.get("auth_type") or "none"
    if kind not in AUTH_TYPES:
        raise HTTPException(400, f"auth_type must be one of {sorted(AUTH_TYPES)}")
    secret = cfg.get("password") or ""
    if kind == "bearer":
        return {"Authorization": f"Bearer {secret}"}, None
    if kind == "header":
        name = (cfg.get("auth_header_name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", name):
            raise HTTPException(400, "Header name may only contain letters, digits and dashes")
        return {name: secret}, None
    if kind == "basic":
        return {}, (cfg.get("username") or "", secret)
    return {}, None


def _get(url: str, headers: dict, basic) -> tuple[bytes, str]:
    """GET with manual redirects (host re-checked per hop) and a streamed size cap."""
    hdrs = {"User-Agent": "BusinessRealityEngine/1.0", "Accept": "text/csv,application/json,application/vnd.ms-excel,*/*", **headers}
    current = url
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current, headers=hdrs, auth=basic) as r:
                    if r.status_code in (301, 302, 303, 307, 308):
                        nxt = urljoin(current, r.headers.get("location", ""))
                        current = validate_url(nxt)
                        if urlparse(current).hostname != urlparse(url).hostname:
                            hdrs = {k: v for k, v in hdrs.items() if k.lower() not in ("authorization",)}
                            basic = None                      # never forward credentials to another host
                        continue
                    if r.status_code in (401, 403):
                        raise HTTPException(502, "The source refused access (check the sharing setting or the key/login)")
                    if r.status_code == 404:
                        raise HTTPException(502, "The source address was not found (404)")
                    if r.status_code >= 400:
                        raise HTTPException(502, f"The source returned an error ({r.status_code})")
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf += chunk
                        if len(buf) > MAX_FILE_SIZE_BYTES:
                            raise HTTPException(413, "The source is larger than the 10MB limit")
                    return bytes(buf), (r.headers.get("content-type") or "").lower()
            except httpx.TimeoutException:
                raise HTTPException(502, "The source took too long to answer")
            except httpx.HTTPError:
                raise HTTPException(502, "Could not reach the source")
    raise HTTPException(502, "Too many redirects")


def _guess_ext(url: str, ctype: str, content: bytes, fmt: str) -> str:
    if fmt in ("csv", "json", "xlsx"):
        return "." + fmt
    path = urlparse(url).path.lower()
    for e in (".xlsx", ".json", ".csv", ".xml", ".tsv", ".txt"):
        if path.endswith(e):
            return e
    if "json" in ctype:
        return ".json"
    if "spreadsheetml" in ctype or content[:2] == b"PK":
        return ".xlsx"
    if "xml" in ctype:
        return ".xml"
    if content.lstrip()[:1] in (b"{", b"["):
        return ".json"
    return ".csv"


def _dig(doc: Any, path: str) -> Any:
    for key in [k for k in path.split(".") if k]:
        if isinstance(doc, dict) and key in doc:
            doc = doc[key]
        else:
            raise HTTPException(400, f"'{key}' was not found in the response while following '{path}'")
    return doc


def _with_param(url: str, **params) -> str:
    p = urlparse(url)
    q = dict(parse_qsl(p.query)); q.update({k: str(v) for k, v in params.items()})
    return urlunparse(p._replace(query=urlencode(q)))


def fetch_dataframe(config: dict, *, max_pages: Optional[int] = None):
    """Returns a DataFrame of the rows at the connector's URL. `config` has secrets still encrypted (as stored)."""
    import pandas as pd
    from app.connectors.parsers import parse_json
    cfg = decrypt_config_secrets(config)
    url = validate_url(cfg.get("url", ""))
    headers, basic = _auth(cfg)
    fmt = (cfg.get("format") or "auto").lower()
    json_path = (cfg.get("json_path") or "").strip()
    paginate = (cfg.get("paginate") or "none") == "page"
    pages = min(int(max_pages or cfg.get("max_pages") or 10), MAX_PAGES) if paginate else 1
    per_page = int(cfg.get("page_size") or 100)

    frames, seen_first = [], None
    for page in range(1, pages + 1):
        target = _with_param(url, page=page, per_page=per_page) if paginate else url
        content, ctype = _get(target, headers, basic)
        ext = _guess_ext(target, ctype, content, fmt)
        if ext == ".json" and json_path:
            try:
                doc = json.loads(content.decode("utf-8-sig"))
            except Exception:
                raise HTTPException(400, "The source did not return valid JSON")
            sub = _dig(doc, json_path)
            if not isinstance(sub, list):
                raise HTTPException(400, f"'{json_path}' in the response is not a list of records")
            if not sub:
                break
            df = parse_json(json.dumps(sub).encode())
        else:
            try:
                df = parse_file_to_dataframe(content, ext)
            except HTTPException as e:
                if paginate and page > 1 and "no data rows" in str(e.detail).lower():
                    break
                raise
        first = hashlib.sha1(df.head(1).to_json().encode()).hexdigest()
        if paginate and first == seen_first:
            break                                          # the API ignores `page`: stop instead of repeating page 1
        seen_first = first
        frames.append(df)
        if not paginate or len(df) < per_page:
            break
    if not frames:
        raise HTTPException(400, "The source returned no data rows")
    return frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
