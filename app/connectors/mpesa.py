"""M-Pesa live payments.

A business with a Paybill or Till number can have Safaricom's Daraja API post every customer payment to a private link
(the C2B "confirmation URL"). The payload is Safaricom's own JSON, so this module only supplies the mapping that turns
it into a sale; the link, its secret token, idempotency and rate limits are the ordinary webhook connector's.

Facts the mapping relies on (Daraja C2B confirmation): the payment's unique id is `TransID`, its time is `TransTime` as
YYYYMMDDHHMMSS in East Africa Time, its amount is the string `TransAmount`, and the payer's number is `MSISDN` (Daraja
may send it masked or hashed, in which case that is what is stored). Only successful payments are sent. Daraja expects
a reply with ResultCode 0, which the webhook endpoint returns for this mapping. Because a payment's `TransID` is its
idempotency key, a payment delivered more than once is still counted once.
"""
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

DARAJA_ACK = {"ResultCode": 0, "ResultDesc": "Accepted"}

C2B_MAPPING = {
    "entity": "sale",
    "event_type": "sale.created",
    "entity_id_path": "TransID",
    "timestamp_path": "TransTime",
    "timestamp_format": "%Y%m%d%H%M%S",
    "amount_path": "TransAmount",
    "customer_id_path": "MSISDN",
    "default_currency": "KES",
    "ack": "daraja",
}

EAT = timezone(timedelta(hours=3))       # Daraja writes TransTime in East Africa Time


def sample_c2b_payload(amount: float = 150.0, trans_id: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    """A payment shaped like Daraja's C2B confirmation. Used to test a source without spending money: by the in-app
    "Test" button (which stores nothing) and by scripts/mpesa_simulate.py (which sends it for real)."""
    now = (now or datetime.now(EAT)).astimezone(EAT)
    trans_id = trans_id or "SIM" + "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
    return {"TransactionType": "Pay Bill", "TransID": trans_id, "TransTime": now.strftime("%Y%m%d%H%M%S"), "TransAmount": f"{amount:.2f}",
            "BusinessShortCode": "600638", "BillRefNumber": "TEST", "InvoiceNumber": "", "OrgAccountBalance": "0.00", "ThirdPartyTransID": "",
            "MSISDN": "254700000000", "FirstName": "Test", "MiddleName": "", "LastName": "Payer"}


PRESETS = {"mpesa_c2b": C2B_MAPPING}


def expand_preset(config: dict) -> dict:
    """Replace {"preset": "<name>"} by that preset's full mapping, so what is stored is exactly what is used."""
    name = config.get("preset")
    if name not in PRESETS:
        raise HTTPException(400, f"Unknown preset '{name}'. Available: {sorted(PRESETS)}")
    return {**PRESETS[name], **{k: v for k, v in config.items() if k != "preset"}}
