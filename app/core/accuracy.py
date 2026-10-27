"""
Section 55: validation and accuracy testing.

  backtest            replay the detectors day by day over history, inside a transaction that is ALWAYS rolled
                      back (nothing is written, no notifications are queued), and compare the alert episodes with
                      the events the owner has labelled as having really happened (GroundTruthEvent).
  false-positive rate per alert type over time, from what people actually did with real alerts.
  calibration         does "confidence 0.7" really mean ~70% of such alerts turned out useful?

Honesty rules: precision/recall are reported only when labelled events exist; precision is a LOWER BOUND (an alert
about something real that nobody labelled counts against it); a calibration bucket with too few closed alerts says
so instead of printing a number.
"""
import math
import statistics
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.alert_engine import kind_of
from app.core.db import engine
from app.models.alerts import Alert, AlertState
from app.models.config import GroundTruthEvent
from app.core.clock import utcnow

MAX_STEPS = 200
MAX_THRESHOLD_VARIANTS = 4
NOT_A_CLAIM_OF_TROUBLE = {"unknown", "opportunity", "silence"}
CLOSED_USEFUL = {AlertState.resolved}
CLOSED_NOT_USEFUL = {AlertState.dismissed, AlertState.false_positive}
MIN_FOR_RATE = 10


def _episodes(fired_by_day: list[tuple[date, dict]], step_days: int) -> list[dict]:
    open_eps: dict[str, dict] = {}
    done: list[dict] = []
    for d, fired in fired_by_day:
        for key, info in fired.items():
            ep = open_eps.get(key)
            if ep and (d - ep["last_day"]).days <= step_days:
                ep["last_day"] = d
                ep["days_firing"] += 1
            else:
                if ep:
                    done.append(ep)
                open_eps[key] = {"key": key, "first_day": d, "last_day": d, "days_firing": 1, **info}
        for key in [k for k, e in open_eps.items() if k not in fired and (d - e["last_day"]).days >= step_days]:
            done.append(open_eps.pop(key))
    return done + list(open_eps.values())


def _match(episodes: list[dict], truths: list[GroundTruthEvent], tolerance_days: int) -> dict:
    matched_truth: dict[str, dict] = {}
    tp_eps = set()
    for t in truths:
        for e in episodes:
            if t.metric and (e.get("metric") or "").split(".")[0] != t.metric.split(".")[0]:
                continue
            if e["first_day"] <= t.ends_on + timedelta(days=tolerance_days) and e["last_day"] >= t.starts_on:
                tp_eps.add(e["key"] + e["first_day"].isoformat())
                best = matched_truth.get(t.id)
                if best is None or e["first_day"] < best["first_day"]:
                    matched_truth[t.id] = e
    return {"matched_truth": matched_truth, "tp_episode_ids": tp_eps}


def _one_pass(scratch: Session, tenant_id: str, metrics: list[dict], start: date, end: date, step_days: int) -> list[dict]:
    from app.core.detectors import run_tenant_cycle
    fired_by_day = []
    d = start
    while d <= end:
        now = datetime.combine(d, time(0)) + timedelta(days=1)        # day d is complete at this moment
        res = run_tenant_cycle(scratch, tenant_id, metrics, now=now, replay=True)
        fired = {}
        for a in res["alerts"]:
            if a is None or a.state not in (AlertState.detected, AlertState.investigating):
                continue
            ev = a.evidence or {}
            k = kind_of(a) or a.kind or "other"
            if k in NOT_A_CLAIM_OF_TROUBLE:
                continue
            fired[ev.get("dedup_key", a.title)] = {"kind": k, "severity": a.severity.value, "title": a.title, "metric": ev.get("metric")}
        fired_by_day.append((d, fired))
        d += timedelta(days=step_days)
    return _episodes(fired_by_day, step_days)


def _score(episodes, truths, tolerance_days, days_covered) -> dict:
    out = {"episodes": len(episodes), "alerts_per_30_days": round(len(episodes) / days_covered * 30, 2) if days_covered else None,
           "by_kind": {}}
    for e in episodes:
        out["by_kind"][e["kind"]] = out["by_kind"].get(e["kind"], 0) + 1
    if not truths:
        out.update({"precision": None, "recall": None, "labelled_events": 0,
                    "note": "No labelled events in this period, so precision and recall cannot be measured. Add known incidents under Accuracy."})
        return out
    m = _match(episodes, truths, tolerance_days)
    detected = len(m["matched_truth"])
    lead = [(e["first_day"] - next(t.starts_on for t in truths if t.id == tid)).days for tid, e in m["matched_truth"].items()]
    out.update({
        "labelled_events": len(truths), "detected_events": detected, "missed_events": [t.label for t in truths if t.id not in m["matched_truth"]],
        "recall": round(detected / len(truths), 3),
        "precision": round(len(m["tp_episode_ids"]) / len(episodes), 3) if episodes else None,
        "precision_is_lower_bound": True,
        "median_days_to_detect": statistics.median(lead) if lead else None,
    })
    p, r = out["precision"], out["recall"]
    out["f1"] = round(2 * p * r / (p + r), 3) if p and r else (0.0 if p is not None else None)
    return out


def run_backtest(tenant_id: str, start: date, end: date, *, step_days: int = 1, tolerance_days: int = 3,
                 threshold_multipliers: Optional[list[float]] = None) -> dict:
    """Replays [start, end]. Raises ValueError on bad input. Never writes (outer transaction is rolled back)."""
    from app.core.scheduler import _metrics_for_tenant
    if end < start:
        raise ValueError("end must be on/after start")
    if step_days < 1 or tolerance_days < 0 or tolerance_days > 30:
        raise ValueError("step_days must be >= 1 and tolerance_days 0..30")
    steps = (end - start).days // step_days + 1
    variants = threshold_multipliers or [1.0]
    if len(variants) > MAX_THRESHOLD_VARIANTS or any(v <= 0 or v > 10 for v in variants):
        raise ValueError(f"at most {MAX_THRESHOLD_VARIANTS} threshold multipliers, each between 0 and 10")
    if steps * len(variants) > MAX_STEPS:
        raise ValueError(f"too much to replay at once ({steps * len(variants)} steps; the limit is {MAX_STEPS}); shorten the range or raise step_days")

    conn = engine.connect()
    outer = conn.begin()
    scratch = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=False)
    try:
        # Isolate the replay from today's real alerts (cooldowns/dedup); all of this is rolled back below.
        scratch.execute(text("UPDATE cases SET alert_id = NULL WHERE tenant_id = :t"), {"t": tenant_id})
        scratch.execute(text("DELETE FROM alerts WHERE tenant_id = :t"), {"t": tenant_id})
        scratch.commit()
        truths = scratch.query(GroundTruthEvent).filter(
            GroundTruthEvent.tenant_id == tenant_id, GroundTruthEvent.ends_on + timedelta(days=tolerance_days) >= start,
            GroundTruthEvent.starts_on <= end).all()
        base_metrics = _metrics_for_tenant(scratch, tenant_id)
        results = []
        for mult in variants:
            if len(results):
                scratch.execute(text("DELETE FROM alerts WHERE tenant_id = :t"), {"t": tenant_id}); scratch.commit()
            metrics = [{**m, "deterioration_threshold": float(m.get("deterioration_threshold", 0.10)) * mult} for m in base_metrics]
            episodes = _one_pass(scratch, tenant_id, metrics, start, end, step_days)
            res = _score(episodes, truths, tolerance_days, (end - start).days + 1)
            res["threshold_multiplier"] = mult
            res["sample_episodes"] = [{**e, "first_day": e["first_day"].isoformat(), "last_day": e["last_day"].isoformat()} for e in episodes[:25]]
            results.append(res)
    finally:
        scratch.close()
        outer.rollback()
        conn.close()
    best = None
    scored = [r for r in results if r.get("f1") is not None]
    if len(results) > 1 and scored:
        # Best F1; on a tie prefer the setting closest to what is configured today (least change), then fewer alerts.
        best = max(scored, key=lambda r: (r["f1"], -abs(math.log(r["threshold_multiplier"])), -r["episodes"]))["threshold_multiplier"]
    return {"range": {"start": start.isoformat(), "end": end.isoformat(), "step_days": step_days}, "tolerance_days": tolerance_days,
            "results": results, "recommended_threshold_multiplier": best,
            "limitations": ["Data-silence and case-outcome checks are not replayed.",
                            "Only events labelled as known incidents can be scored; unlabelled real problems count against precision.",
                            "Replay uses today's configuration (monitored metrics and thresholds), not what was configured back then."]}


# ---------------- live accuracy ----------------

def _bucket(c: float) -> str:
    return "0.0-0.4" if c < 0.4 else "0.4-0.6" if c < 0.6 else "0.6-0.8" if c < 0.8 else "0.8-1.0"


def live_accuracy(db: Session, tenant_id: str, days: int = 180, now: Optional[datetime] = None, tolerance_days: int = 3) -> dict:
    now = now or utcnow()
    since = now - timedelta(days=days)
    alerts = db.query(Alert).filter(Alert.tenant_id == tenant_id, Alert.created_at >= since).all()
    by_kind: dict[str, dict] = {}
    by_month: dict[str, dict] = {}
    buckets: dict[str, dict] = {}
    for a in alerts:
        k = kind_of(a) or a.kind or "other"
        if k in NOT_A_CLAIM_OF_TROUBLE:
            continue
        closed = a.state in CLOSED_USEFUL | CLOSED_NOT_USEFUL
        useful = a.state in CLOSED_USEFUL
        for table, key in ((by_kind, k), (by_month, a.created_at.strftime("%Y-%m"))):
            row = table.setdefault(key, {"alerts": 0, "closed": 0, "false_positive": 0, "dismissed": 0, "resolved": 0})
            row["alerts"] += 1
            if closed:
                row["closed"] += 1
                row[a.state.value] += 1
        if closed and a.confidence is not None:
            b = buckets.setdefault(_bucket(float(a.confidence)), {"closed": 0, "useful": 0, "conf_sum": 0.0})
            b["closed"] += 1; b["useful"] += int(useful); b["conf_sum"] += float(a.confidence)

    def rate(row):
        row["false_positive_rate"] = round(row["false_positive"] / row["closed"], 3) if row["closed"] >= MIN_FOR_RATE else None
        row["not_useful_rate"] = round((row["false_positive"] + row["dismissed"]) / row["closed"], 3) if row["closed"] >= MIN_FOR_RATE else None
        if row["closed"] < MIN_FOR_RATE:
            row["note"] = f"only {row['closed']} closed alert(s); rates need at least {MIN_FOR_RATE}"
        return row
    calibration = []
    for name in sorted(buckets):
        b = buckets[name]
        avg_conf = b["conf_sum"] / b["closed"]
        row = {"bucket": name, "closed": b["closed"], "average_confidence": round(avg_conf, 3)}
        if b["closed"] >= MIN_FOR_RATE:
            acc = b["useful"] / b["closed"]
            gap = avg_conf - acc
            row.update({"observed_useful_rate": round(acc, 3), "gap": round(gap, 3),
                        "verdict": "overconfident" if gap > 0.15 else "underconfident" if gap < -0.15 else "well calibrated"})
        else:
            row["verdict"] = "not enough closed alerts to judge"
        calibration.append(row)

    truths = db.query(GroundTruthEvent).filter(GroundTruthEvent.tenant_id == tenant_id, GroundTruthEvent.ends_on >= since.date(),
                                               GroundTruthEvent.starts_on <= now.date()).all()
    recall = None
    if truths:
        hit, missed = 0, []
        for t in truths:
            lo = datetime.combine(t.starts_on, time(0))
            hi = datetime.combine(t.ends_on + timedelta(days=tolerance_days + 1), time(0))
            found = any(lo <= a.created_at < hi and (kind_of(a) or a.kind) not in NOT_A_CLAIM_OF_TROUBLE
                        and (not t.metric or str((a.evidence or {}).get("metric", "")).split(".")[0] == t.metric.split(".")[0]) for a in alerts)
            hit += int(found)
            if not found:
                missed.append(t.label)
        recall = {"labelled_events": len(truths), "detected": hit, "recall": round(hit / len(truths), 3), "missed": missed}
    return {"period_days": days, "by_kind": {k: rate(v) for k, v in sorted(by_kind.items())},
            "by_month": {k: rate(v) for k, v in sorted(by_month.items())}, "calibration": calibration, "live_recall": recall,
            "method": "An alert counts as useful only when a person resolved it; dismissed and false-positive count against it. "
                      "Alerts still open are excluded."}
