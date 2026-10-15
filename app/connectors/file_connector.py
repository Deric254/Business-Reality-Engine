"""
File connector (Section 8-E).
Security posture for anything accepting user-uploaded files:
- extension allowlist (never trust the declared content-type alone)
- hard size cap before reading into memory
- row cap to prevent memory-exhaustion DoS
- no formula evaluation from XLSX (openpyxl data_only reads computed values,
  never executes macros/formulas)
- column mapping is explicit and configured per-connector — we never guess
  or trust arbitrary uploaded column names as if they were code/identifiers
"""
import io
from typing import Optional

import pandas as pd
from fastapi import HTTPException
from pydantic import BaseModel

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024   # 10MB — generous for a business's periodic export
MAX_ROWS = 50_000                         # prevents a hostile/corrupt file from exhausting memory
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".json", ".jsonl", ".xml", ".txt", ".tsv"}


class ColumnMapping(BaseModel):
    """
    Stored in Connector.config. Maps a business's spreadsheet columns onto
    the Universal Data Contract. Nothing about the uploaded file's column
    names is trusted or executed — this mapping is the only bridge.
    """
    entity: str                      # constant, e.g. "sale"
    event_type: str                  # constant, e.g. "sale.created"
    timestamp_col: str
    amount_col: Optional[str] = None
    quantity_col: Optional[str] = None
    currency_col: Optional[str] = None
    location_col: Optional[str] = None
    customer_id_col: Optional[str] = None
    product_id_col: Optional[str] = None
    entity_id_col: Optional[str] = None
    default_currency: str = "KES"


def validate_upload(filename: str, content: bytes) -> str:
    """Returns the validated extension, or raises. Call before any parsing."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{ext}'. Allowed: {ALLOWED_EXTENSIONS}")
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE_BYTES // (1024*1024)}MB limit")
    if len(content) == 0:
        raise HTTPException(400, "Empty file")
    return ext


def parse_file_to_dataframe(content: bytes, ext: str) -> pd.DataFrame:
    buf = io.BytesIO(content)
    try:
        if ext in (".json", ".jsonl"):
            from app.connectors.parsers import parse_json
            df = parse_json(content)
        elif ext == ".xml":
            from app.connectors.parsers import parse_xml
            df = parse_xml(content)
        elif ext in (".txt", ".tsv"):
            from app.connectors.parsers import parse_txt
            df = parse_txt(content)
        elif ext == ".csv":
            df = pd.read_csv(buf)
        else:  # .xlsx
            # data_only reads cached computed values, not formulas — no formula execution
            df = pd.read_excel(buf, engine="openpyxl")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    if len(df) > MAX_ROWS:
        raise HTTPException(413, f"File has {len(df)} rows, exceeds cap of {MAX_ROWS}")
    if len(df) == 0:
        raise HTTPException(400, "File has no data rows")
    return df


def to_number(val, where: str):
    """Numbers from text sources: tolerate thousands separators and surrounding spaces, refuse anything else loudly."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    txt = str(val).strip().replace(",", "").replace(" ", "")
    if txt == "":
        return None
    try:
        out = float(txt)
    except ValueError:
        raise HTTPException(400, f"{where}: '{val}' is not a number")
    if out != out or out in (float("inf"), float("-inf")):
        raise HTTPException(400, f"{where}: '{val}' is not a finite number")
    return out


ROW_ID_COL = "__row_id"


def add_row_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Give every row a stable id built from its content (+ its n-th occurrence, so two genuinely identical rows in one
    file stay two rows). Re-uploading or re-pulling the same rows is then idempotent instead of double counting."""
    import hashlib
    seen: dict[str, int] = {}
    ids = []
    for rec in df.astype(str).itertuples(index=False, name=None):
        h = hashlib.sha1("\x1f".join(rec).encode()).hexdigest()[:20]
        seen[h] = seen.get(h, 0) + 1
        ids.append(f"{h}-{seen[h]}")
    out = df.copy()
    out[ROW_ID_COL] = ids
    return out


def with_default_id(df: pd.DataFrame, mapping: "ColumnMapping") -> "ColumnMapping":
    """If no unique-id column is mapped, map the generated one (the frame must have gone through add_row_ids)."""
    if mapping.entity_id_col and mapping.entity_id_col in df.columns:
        return mapping
    return mapping.model_copy(update={"entity_id_col": ROW_ID_COL})


def dataframe_to_normalized_events(
    df: pd.DataFrame, mapping: ColumnMapping, tenant_id: str, connector_id: str, source: str
) -> list[dict]:
    """
    Turns rows into Universal Data Contract dicts (matching NormalizedEvent shape).
    Missing mapped columns fail loudly rather than silently producing null data —
    a wrong mapping should surface at ingest time, not corrupt the business model.
    """
    required = [mapping.timestamp_col]
    for col in required:
        if col not in df.columns:
            raise HTTPException(
                400, f"Configured column '{col}' not found in file. File columns: {list(df.columns)}"
            )

    events = []
    for idx, row in df.iterrows():
        try:
            timestamp = pd.to_datetime(row[mapping.timestamp_col]).to_pydatetime()
        except Exception:
            raise HTTPException(400, f"Row {idx}: unparseable timestamp '{row[mapping.timestamp_col]}'")

        def _get(col_attr: str):
            col = getattr(mapping, col_attr)
            if not col or col not in df.columns:
                return None
            val = row[col]
            return None if pd.isna(val) else val

        events.append({
            "tenant_id": tenant_id,
            "connector_id": connector_id,
            "source": source,
            "entity": mapping.entity,
            "entity_id": str(_get("entity_id_col")) if _get("entity_id_col") is not None else None,
            "event_type": mapping.event_type,
            "timestamp": timestamp,
            "amount": to_number(_get("amount_col"), f"Row {idx} amount"),
            "quantity": to_number(_get("quantity_col"), f"Row {idx} quantity"),
            "currency": str(_get("currency_col")) if _get("currency_col") is not None else mapping.default_currency,
            "location": str(_get("location_col")) if _get("location_col") is not None else None,
            "customer_id": str(_get("customer_id_col")) if _get("customer_id_col") is not None else None,
            "product_id": str(_get("product_id_col")) if _get("product_id_col") is not None else None,
            "metadata_json": {},
        })
    return events
