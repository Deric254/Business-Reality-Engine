"""
Section 8-E: JSON / XML / TXT parsers. Every parser returns a DataFrame that then goes through the SAME
column-mapping path as CSV/XLSX, so a file is never more trusted because of its format.

  JSON  an array of objects, {"data"|"rows"|"records"|"items"|"results": [...]}, or JSON Lines (one object per line).
        Nested objects are flattened to dotted column names (customer.id), to a bounded depth.
  XML   the repeating child element is the row; its attributes and child elements are the columns. Parsed with
        defusedxml, so entity expansion / external entities / DTD tricks are rejected.
  TXT   delimited text (comma, semicolon, pipe or tab) with a header row; the delimiter is detected, never executed.
"""
import csv
import io
import json
from typing import Any

import pandas as pd
from fastapi import HTTPException

MAX_ROWS = 50_000
MAX_DEPTH = 4
MAX_COLUMNS = 200
_WRAPPER_KEYS = ("data", "rows", "records", "items", "results", "events")


def _bad(msg: str):
    raise HTTPException(400, msg)


def _flatten(obj: Any, prefix: str = "", depth: int = 0) -> dict:
    out: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            if isinstance(v, dict) and depth < MAX_DEPTH:
                out.update(_flatten(v, key + ".", depth + 1))
            elif isinstance(v, (dict, list)):
                out[key] = json.dumps(v, default=str)[:2000]         # too deep / a list: keep as text, never expand
            else:
                out[key] = v
    return out


def _frame(rows: list[dict], what: str) -> pd.DataFrame:
    if not rows:
        _bad(f"{what} has no data rows")
    if len(rows) > MAX_ROWS:
        raise HTTPException(413, f"{what} has {len(rows)} rows, exceeds cap of {MAX_ROWS}")
    df = pd.DataFrame(rows)
    if df.shape[1] > MAX_COLUMNS:
        _bad(f"{what} has {df.shape[1]} columns, more than the {MAX_COLUMNS} allowed")
    return df


def parse_json(content: bytes) -> pd.DataFrame:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        _bad("JSON file is not valid UTF-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        try:
            doc = [json.loads(ln) for ln in lines]            # JSON Lines
        except json.JSONDecodeError as e:
            _bad(f"Could not parse JSON: {e.msg} (line {e.lineno})")
    if isinstance(doc, dict):
        for key in _WRAPPER_KEYS:
            if isinstance(doc.get(key), list):
                doc = doc[key]
                break
        else:
            doc = [doc]
    if not isinstance(doc, list) or not all(isinstance(r, dict) for r in doc):
        _bad("JSON must be an array of objects (or an object wrapping one under data/rows/records/items/results)")
    return _frame([_flatten(r) for r in doc], "JSON file")


def parse_xml(content: bytes) -> pd.DataFrame:
    from defusedxml import ElementTree as ET
    from defusedxml.common import DefusedXmlException
    try:
        root = ET.fromstring(content)
    except DefusedXmlException:
        _bad("XML contains forbidden constructs (entities or external references)")
    except ET.ParseError as e:
        _bad(f"Could not parse XML: {e}")

    def strip(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
    children = list(root)
    if not children:
        _bad("XML has no data rows")
    # The row element is the most common child tag (e.g. <sales><sale/>...</sales>).
    counts: dict[str, int] = {}
    for c in children:
        counts[strip(c.tag)] = counts.get(strip(c.tag), 0) + 1
    row_tag = max(counts, key=counts.get)
    rows = []
    for el in children:
        if strip(el.tag) != row_tag:
            continue
        row = {f"@{strip(k)}": v for k, v in el.attrib.items()}
        for ch in el:
            if len(ch):                                  # nested element: flatten one level into dotted names
                for gc in ch:
                    row[f"{strip(ch.tag)}.{strip(gc.tag)}"] = (gc.text or "").strip() or None
            else:
                row[strip(ch.tag)] = (ch.text or "").strip() or None
        if not row and (el.text or "").strip():
            row["value"] = el.text.strip()
        rows.append(row)
    return _frame(rows, "XML file")


def parse_txt(content: bytes) -> pd.DataFrame:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    sample = "\n".join(text.splitlines()[:20])
    if not sample.strip():
        _bad("Text file is empty")
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;|\t").delimiter
    except csv.Error:
        _bad("Could not detect a delimiter; use comma, semicolon, pipe or tab separated text with a header row")
    try:
        df = pd.read_csv(io.StringIO(text), sep=delim, dtype=str, keep_default_na=True, engine="python", on_bad_lines="skip")
    except Exception as e:
        _bad(f"Could not parse text file: {e}")
    if df.shape[1] < 2:
        _bad("Text file needs a header row and at least two columns")
    if len(df) > MAX_ROWS:
        raise HTTPException(413, f"Text file has {len(df)} rows, exceeds cap of {MAX_ROWS}")
    if len(df) == 0:
        _bad("Text file has no data rows")
    df.columns = [str(c).strip() for c in df.columns]
    return df
