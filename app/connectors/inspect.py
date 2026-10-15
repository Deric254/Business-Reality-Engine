"""Look at a file or URL and suggest how its columns map onto the data contract, so setup is "confirm" not "type".
Suggestions are heuristics on column NAMES and the first rows; the person always sees and can change them."""
import re
from typing import Any

import pandas as pd

_RULES: list[tuple[str, list[str]]] = [
    ("timestamp_col", ["timestamp", "datetime", "date_created", "created_at", "created", "sold_at", "sale_date", "date", "time", "posted", "paid_at"]),
    ("amount_col", ["grand_total", "total_amount", "total", "amount", "revenue", "sales", "subtotal", "price", "value", "cost", "debit", "credit"]),
    ("quantity_col", ["quantity", "qty", "units", "count", "pieces"]),
    ("currency_col", ["currency", "ccy"]),
    ("location_col", ["branch", "store", "shop", "outlet", "location", "site", "warehouse"]),
    ("customer_id_col", ["customer", "client", "buyer", "patient", "member", "account"]),
    ("product_id_col", ["product", "item", "sku", "description", "drug", "service", "category"]),
    ("entity_id_col", ["transaction_id", "receipt", "invoice", "order_id", "order_number", "reference", "ref", "txn", "id", "number"]),
]
_EXPENSE_HINT = re.compile(r"expense|cost|payment_out|supplier|vendor|purchase|bill", re.I)


def _norm(c: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_")


def suggest_mapping(df: pd.DataFrame) -> dict[str, Any]:
    cols = [str(c) for c in df.columns if not str(c).startswith("__")]
    norm = {c: _norm(c) for c in cols}
    used: set[str] = set()
    out: dict[str, Any] = {}
    for field, words in _RULES:
        best, best_rank = None, 10 ** 6
        for c in cols:
            if c in used:
                continue
            n = norm[c]
            for rank, w in enumerate(words):
                hit = n == w or (len(w) > 3 and w in n) or (w in ("id", "ref", "date", "time") and n.endswith("_" + w))
                if hit and rank < best_rank:
                    best, best_rank = c, rank
                    break
        if field == "amount_col" and best is not None:
            if not pd.to_numeric(df[best].astype(str).str.replace(r"[,\s]", "", regex=True), errors="coerce").notna().mean() > 0.6:
                best = None
        if field == "timestamp_col" and best is not None:
            if pd.to_datetime(df[best], errors="coerce", format="mixed").notna().mean() < 0.6:
                best = None
        if best is not None:
            out[field] = best
            used.add(best)
    if "timestamp_col" not in out:               # fall back to any column that parses as dates
        for c in cols:
            if c not in used and df[c].dtype == object or str(df[c].dtype).startswith("datetime"):
                if pd.to_datetime(df[c], errors="coerce", format="mixed").notna().mean() > 0.8:
                    out["timestamp_col"] = c; used.add(c); break
    joined = " ".join(norm.values())
    out["entity"] = "expense" if _EXPENSE_HINT.search(joined) and not re.search(r"sale|customer|revenue|order", joined) else "sale"
    out["event_type"] = f"{out['entity']}.created"
    return out


def describe(df: pd.DataFrame, sample: int = 5) -> dict[str, Any]:
    shown = df[[c for c in df.columns if not str(c).startswith("__")]]
    preview = shown.head(sample).astype(object).where(shown.head(sample).notna(), None).to_dict(orient="records")
    return {
        "columns": [str(c) for c in shown.columns],
        "row_count": int(len(df)),
        "sample": [{str(k): (v if isinstance(v, (int, float, str, bool)) or v is None else str(v)) for k, v in r.items()} for r in preview],
        "suggested_mapping": suggest_mapping(shown),
    }
