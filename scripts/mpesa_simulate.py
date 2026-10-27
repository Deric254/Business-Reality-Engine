"""Send test payments to your M-Pesa source, shaped like Safaricom's Daraja C2B confirmations, without spending money.

    python scripts/mpesa_simulate.py --url https://your-server/api/webhooks/<connector>/<token> [--amount 150] [--count 3]

Use it right after you add the "M-Pesa Paybill or Till (live)" source: it proves the link, the reply and the sale that
appears in the app, before Safaricom is involved. (Safaricom's own sandbox can then be pointed at the same link.) Each
call uses a fresh transaction id, so each is a new sale. Pass --repeat to send the identical payment twice and see that
it is counted once. The shape is Daraja's documented C2B confirmation; real traffic has not been checked by the authors."""
import argparse
import random
import string
import sys
from datetime import datetime, timedelta, timezone

import httpx

EAT = timezone(timedelta(hours=3))        # Daraja writes TransTime in East Africa Time


def build_payload(amount: float, trans_id: str | None = None, now: datetime | None = None) -> dict:
    now = (now or datetime.now(EAT)).astimezone(EAT)
    trans_id = trans_id or "SIM" + "".join(random.choices(string.ascii_uppercase + string.digits, k=7))
    return {"TransactionType": "Pay Bill", "TransID": trans_id, "TransTime": now.strftime("%Y%m%d%H%M%S"),
            "TransAmount": f"{amount:.2f}", "BusinessShortCode": "600638", "BillRefNumber": "TEST", "InvoiceNumber": "",
            "OrgAccountBalance": "0.00", "ThirdPartyTransID": "", "MSISDN": "254700000000",
            "FirstName": "Test", "MiddleName": "", "LastName": "Payer"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="the private link shown once when the source was created")
    ap.add_argument("--amount", type=float, default=100.0)
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--repeat", action="store_true", help="send each payment twice")
    a = ap.parse_args()
    bad = 0
    with httpx.Client(timeout=30) as c:
        for _ in range(a.count):
            payload = build_payload(a.amount)
            for attempt in range(2 if a.repeat else 1):
                r = c.post(a.url, json=payload)
                ok = r.status_code == 200 and r.json().get("ResultCode") == 0
                bad += not ok
                print(f"{payload['TransID']} (send {attempt + 1}): HTTP {r.status_code} {'accepted' if ok else r.text[:200]}")
    print("All accepted." if not bad else f"{bad} payment(s) were not accepted.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
