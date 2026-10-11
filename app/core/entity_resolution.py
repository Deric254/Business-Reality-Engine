"""
Entity Resolution (Section 13).

Design constraint learned by testing, not assumed up front: initials are
NOT a safe basis for auto-linking. "J. Mwangi" matches "John Mwangi" on
surname + initial — but it ALSO matches "Jane Mwangi" on the same basis.
Two different people, same false-positive risk. So:

- ONLY an exact match (after normalization) auto-links. Confidence 1.0,
  no ambiguity possible.
- Everything else — surname matches, initials, fuzzy similarity — is
  surfaced as a ranked suggestion with needs_review=True. A human decides
  via the /merge endpoint (Section 13: "maintain confidence scores and
  allow corrections"). The engine never guesses on a person's behalf.

This trades some auto-linking convenience for not silently corrupting a
customer's transaction history by merging them with someone else.
"""
import re
import uuid
from functools import lru_cache
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from sqlalchemy import text, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from rapidfuzz import fuzz, process

from app.models.core import ResolvedEntity, EntityAlias

SUGGESTION_THRESHOLD = 55   # below this, too weak to even suggest
SURNAME_MATCH_BONUS = 25    # ranking boost when last tokens match/abbreviate — suggestion only, never auto-link


@lru_cache(maxsize=200_000)
def _normalize(value: str) -> str:
    return re.sub(r"[^\w\s]", "", value or "").strip().lower()


@lru_cache(maxsize=200_000)
def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_normalize(value).split())


def _surname_signal(a: str, b: str) -> bool:
    """
    True if the last tokens look like the same surname — full match, or one
    is a single-letter abbreviation of the other. Used ONLY to rank
    suggestions higher; never a basis for auto-linking (see module docstring).
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    sa, sb = ta[-1], tb[-1]
    if sa == sb:
        return True
    if len(sa) == 1 and sb.startswith(sa):
        return True
    if len(sb) == 1 and sa.startswith(sb):
        return True
    return False


@dataclass
class ResolutionResult:
    resolved_entity_id: str
    confidence: float
    matched_existing: bool
    needs_review: bool
    suggestions: list[dict] = field(default_factory=list)


SUGGESTION_BATCH = 250   # values per Tier-2 query


def _last_token(norm: str) -> str:
    toks = norm.split()
    return toks[-1] if toks else ""


def _score(raw_value: str, names: list[str]) -> tuple[float, bool]:
    best = process.extractOne(raw_value, names, scorer=fuzz.token_sort_ratio)[1] if len(names) > 1 \
        else fuzz.token_sort_ratio(raw_value, names[0])
    if best < SUGGESTION_THRESHOLD - SURNAME_MATCH_BONUS:
        return best, False          # cannot reach the threshold even with the bonus; skip the surname work
    signal = any(_surname_signal(raw_value, n) for n in names)
    return min(99, best + (SURNAME_MATCH_BONUS if signal else 0)), signal


def _db_suggestions(db: Session, tenant_id: str, entity_type: str, raw_value: str, norm: str) -> list[dict]:
    """
    Tier 2 against EXISTING entities without scanning the tenant. Candidate
    retrieval is indexed and BOUNDED (40 nearest by pg_trgm KNN within the
    tenant, plus
    same-surname / single-letter-abbreviation matches via the last_token
    index), capped at 100 entities, in ONE query that also returns every
    name of each candidate. Ranking then uses the same scoring as always
    (token_sort_ratio + surname bonus). Known difference from the old full
    scan: a pair with fuzzy-ratio >= 55 that is not among the 40 nearest trigram
    neighbours and has a different surname is no longer surfaced — a weak suggestion anyway.
    """
    lt = _last_token(norm)
    rows = db.execute(text("""
        WITH cand AS (
            SELECT entity_id, min(rank) AS rank FROM (
                (SELECT entity_id, 0 AS rank FROM entity_aliases
                 WHERE tenant_id = CAST(:t AS uuid) AND entity_type = :ty
                 ORDER BY normalized_value <-> :n LIMIT 40)
                UNION ALL
                (SELECT entity_id, 1 AS rank FROM entity_aliases
                 WHERE tenant_id = CAST(:t AS uuid) AND entity_type = :ty AND last_token = ANY(:lts)
                 LIMIT 40)
                UNION ALL
                (SELECT entity_id, 1 AS rank FROM entity_aliases
                 WHERE :single AND tenant_id = CAST(:t AS uuid) AND entity_type = :ty AND last_token LIKE :pref
                 LIMIT 40)
            ) u GROUP BY entity_id)
        SELECT a.entity_id, a.raw_value, e.canonical_name
        FROM cand c
        JOIN entity_aliases a ON a.entity_id = c.entity_id
        JOIN resolved_entities e ON e.id = c.entity_id
    """), {
        "t": tenant_id, "ty": entity_type, "n": norm,
        "lts": [x for x in {lt, lt[:1]} if x], "single": len(lt) == 1, "pref": f"{lt}%",
    }).fetchall()
    names: dict[str, list[str]] = {}
    canon: dict[str, str] = {}
    for ent_id, alias_raw, canonical in rows:
        ent_id = str(ent_id)
        names.setdefault(ent_id, [canonical]).append(alias_raw)
        canon[ent_id] = canonical
    out = []
    for ent_id, ns in names.items():
        score, signal = _score(raw_value, ns)
        if score >= SUGGESTION_THRESHOLD:
            out.append({"resolved_entity_id": ent_id, "canonical_name": canon[ent_id],
                        "similarity": round(score, 1), "surname_signal": signal})
    return out


def _db_suggestions_bulk(db: Session, tenant_id: str, entity_type: str, items: list[tuple[str, str]]) -> dict[str, list[dict]]:
    """
    Tier 2 for MANY values in one round trip. `items` = [(raw_value, normalized)] (unique raws).
    Same candidate rules and scoring as _db_suggestions, but retrieval is a single query: a
    VALUES-style unnest joined LATERALLY to the indexed lookups (KNN, surname, abbreviation), so
    the per-value network/parse/plan cost is paid once per chunk instead of once per value.
    Returns {raw_value: [suggestion, ...]} (unsorted, unfiltered by top-3).
    """
    items = [(r, n) for r, n in items if n]
    if not items:
        return {}
    lts = [_last_token(n) for _, n in items]
    rows = db.execute(text("""
        WITH q AS (
            SELECT t.i, t.n, t.lt, left(t.lt, 1) AS lt1, (length(t.lt) = 1) AS single
            FROM unnest(CAST(:idx AS int[]), CAST(:norms AS text[]), CAST(:lts AS text[])) AS t(i, n, lt)
        ),
        cand AS (
            SELECT i, entity_id FROM (
                SELECT q.i, c.entity_id FROM q CROSS JOIN LATERAL (
                    SELECT entity_id FROM entity_aliases
                    WHERE tenant_id = CAST(:t AS uuid) AND entity_type = :ty
                    ORDER BY normalized_value <-> q.n LIMIT 40) c
                UNION ALL
                SELECT q.i, c.entity_id FROM q CROSS JOIN LATERAL (
                    SELECT entity_id FROM entity_aliases
                    WHERE tenant_id = CAST(:t AS uuid) AND entity_type = :ty
                      AND q.lt <> '' AND last_token = ANY(ARRAY[q.lt, q.lt1]) LIMIT 40) c
                UNION ALL
                SELECT q.i, c.entity_id FROM q CROSS JOIN LATERAL (
                    SELECT entity_id FROM entity_aliases
                    WHERE q.single AND tenant_id = CAST(:t AS uuid) AND entity_type = :ty
                      AND last_token LIKE q.lt || '%' LIMIT 40) c
            ) u GROUP BY i, entity_id
        )
        SELECT c.i, a.entity_id, a.raw_value, e.canonical_name
        FROM cand c
        JOIN entity_aliases a ON a.entity_id = c.entity_id
        JOIN resolved_entities e ON e.id = c.entity_id
    """), {"t": tenant_id, "ty": entity_type, "idx": list(range(len(items))),
           "norms": [n for _, n in items], "lts": lts}).fetchall()
    per: dict[int, dict[str, list[str]]] = {}
    canon: dict[str, str] = {}
    for i, ent_id, alias_raw, canonical in rows:
        ent_id = str(ent_id)
        per.setdefault(i, {}).setdefault(ent_id, [canonical]).append(alias_raw)
        canon[ent_id] = canonical
    out: dict[str, list[dict]] = {}
    for i, ents in per.items():
        raw = items[i][0]
        res = []
        for ent_id, ns in ents.items():
            score, signal = _score(raw, ns)
            if score >= SUGGESTION_THRESHOLD:
                res.append({"resolved_entity_id": ent_id, "canonical_name": canon[ent_id],
                            "similarity": round(score, 1), "surname_signal": signal})
        out[raw] = res
    return out


def resolve_entities_bulk(
    db: Session,
    keys: list[tuple[str, str, str, str]],
    attributes_by_key: Optional[dict] = None,
    _depth: int = 0,
    _locked: bool = False,
) -> dict[tuple, ResolutionResult]:
    """
    Resolve many (tenant_id, entity_type, source, raw_value) keys in one pass
    with a handful of queries — the engine behind resolve_entity and
    ingestion. Changes are flushed, NOT committed; the caller owns the
    transaction. Same rules as ever: Tier 0 (known source+raw), Tier 1
    (exact normalized match, auto-link), Tier 2 (ranked suggestions only,
    never auto-link). Values that normalize identically WITHIN this call
    link to the first one, like sequential resolution would.
    """
    attributes_by_key = attributes_by_key or {}
    keys = list(dict.fromkeys(keys))
    results: dict[tuple, ResolutionResult] = {}
    if not keys:
        return results

    groups: dict[tuple, list[tuple]] = {}
    for k in keys:
        groups.setdefault((k[0], k[1]), []).append(k)

    # Sorted so every transaction takes advisory locks in the same order (no deadlocks).
    for (tenant_id, entity_type), gkeys in sorted(groups.items()):
        # Tier 0
        pairs = [(k[2], k[3]) for k in gkeys]
        hit = {}
        for src, raw, ent in db.query(EntityAlias.source, EntityAlias.raw_value, EntityAlias.entity_id).filter(
            EntityAlias.tenant_id == tenant_id, EntityAlias.entity_type == entity_type,
            tuple_(EntityAlias.source, EntityAlias.raw_value).in_(pairs),
        ):
            hit[(src, raw)] = ent
        remaining = []
        for k in gkeys:
            if (k[2], k[3]) in hit:
                results[k] = ResolutionResult(hit[(k[2], k[3])], 1.0, True, False)
            else:
                remaining.append(k)
        if not remaining:
            continue

        # Tier 1: exact normalized match against any known name of this type.
        norms = {k: _normalize(k[3]) for k in remaining}
        known: dict[str, str] = {}
        wanted = [n for n in set(norms.values()) if n]
        if wanted:
            for norm, ent in db.query(EntityAlias.normalized_value, EntityAlias.entity_id).filter(
                EntityAlias.tenant_id == tenant_id, EntityAlias.entity_type == entity_type,
                EntityAlias.normalized_value.in_(wanted),
            ).order_by(EntityAlias.entity_id.desc()):
                known[norm] = ent  # desc order -> the smallest entity_id wins (deterministic)
        alias_rows, new_reps, linked_to_rep = [], [], []
        rep_for_norm: dict[str, tuple] = {}
        for k in remaining:
            norm = norms[k]
            if norm and norm in known:
                alias_rows.append((k, known[norm]))
                results[k] = ResolutionResult(known[norm], 1.0, True, False)
            elif norm and norm in rep_for_norm:
                linked_to_rep.append((k, rep_for_norm[norm]))
            else:
                new_reps.append(k)
                if norm:
                    rep_for_norm[norm] = k

        # Creating entities is serialized per (tenant, type) by a transaction-scoped
        # advisory lock, then the group is re-resolved UNDER the lock so concurrent
        # senders see each other's entities instead of racing to create duplicates
        # (the unique index below remains the safety net). Lookups above stay
        # lock-free, so the common all-hits case never waits.
        if new_reps and not _locked:
            db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                       {"k": f"entities:{tenant_id}:{entity_type}"})
            results.update(resolve_entities_bulk(db, gkeys, attributes_by_key, _depth, _locked=True))
            continue

        # Tier 2 suggestions (existing entities, then earlier new ones in this call).
        suggestions: dict[tuple, list[dict]] = {}
        earlier: list[tuple[tuple, str]] = []   # (key, raw) of new entities created earlier in this call
        db_sugg: dict[str, list[dict]] = {}
        uniq = list({k[3]: norms[k] for k in new_reps if norms[k]}.items())
        for off in range(0, len(uniq), SUGGESTION_BATCH):          # bounded query size
            db_sugg.update(_db_suggestions_bulk(db, tenant_id, entity_type, uniq[off:off + SUGGESTION_BATCH]))
        for k in new_reps:
            sugg = [dict(x) for x in db_sugg.get(k[3], [])]
            if earlier:
                # C-speed prefilter over earlier new values. Exact: score = min(99, ratio + bonus), so a pair can only
                # reach the threshold when its plain ratio is at least threshold - bonus.
                hits = process.extract(k[3], [e[1] for e in earlier], scorer=fuzz.token_sort_ratio,
                                       score_cutoff=max(0, SUGGESTION_THRESHOLD - SURNAME_MATCH_BONUS), limit=None)
                for _, ratio, idx in sorted(hits, key=lambda h: h[2]):      # insertion order, as before
                    ek, eraw = earlier[idx]
                    signal = _surname_signal(k[3], eraw)
                    score = min(99, ratio + (SURNAME_MATCH_BONUS if signal else 0))
                    if score >= SUGGESTION_THRESHOLD:
                        sugg.append({"resolved_entity_id": ("pending", ek), "canonical_name": eraw,
                                     "similarity": round(score, 1), "surname_signal": signal})
            sugg.sort(key=lambda s: -s["similarity"])
            suggestions[k] = sugg[:3]
            earlier.append((k, k[3]))

        # Create new entities + all aliases in bulk.
        ent_id = {k: str(uuid.uuid4()) for k in new_reps}
        if new_reps:
            db.execute(pg_insert(ResolvedEntity.__table__), [
                {"id": ent_id[k], "tenant_id": tenant_id, "entity_type": entity_type, "canonical_name": k[3],
                 "attributes": attributes_by_key.get(k) or {}, "confidence": 1.0, "created_at": datetime.utcnow()}
                for k in new_reps
            ])
        for k in new_reps:
            alias_rows.append((k, ent_id[k]))
        for k, rep in linked_to_rep:
            alias_rows.append((k, ent_id[rep]))
            results[k] = ResolutionResult(ent_id[rep], 1.0, True, False)

        inserted = set()
        if alias_rows:
            stmt = (
                pg_insert(EntityAlias)
                .on_conflict_do_nothing(index_elements=[
                    EntityAlias.tenant_id, EntityAlias.entity_type, EntityAlias.source, EntityAlias.raw_value])
                .returning(EntityAlias.source, EntityAlias.raw_value)
            )
            inserted = {(r[0], r[1]) for r in db.execute(stmt, [
                {"id": str(uuid.uuid4()), "entity_id": e, "tenant_id": tenant_id, "entity_type": entity_type,
                 "source": k[2], "raw_value": k[3], "normalized_value": norms[k],
                 "last_token": _last_token(norms[k]), "confidence": 1.0}
                for k, e in alias_rows
            ])}

        # Concurrent creation: another worker inserted the same (source, raw) first.
        # Undo our entity for that value and converge on the winner's.
        lost = [k for k, _ in alias_rows if (k[2], k[3]) not in inserted]
        if lost:
            lost_reps = [k for k in new_reps if k in lost]
            if lost_reps:
                lost_ids = [ent_id[k] for k in lost_reps]
                db.query(EntityAlias).filter(EntityAlias.entity_id.in_(lost_ids)).delete(synchronize_session=False)
                db.query(ResolvedEntity).filter(ResolvedEntity.id.in_(lost_ids)).delete(synchronize_session=False)
                for k in new_reps:  # reps that won but whose linked keys referenced a lost rep are re-resolved below
                    pass
            if _depth >= 3:
                raise RuntimeError("entity resolution did not converge under concurrent creation")
            redo = [k for k in lost] + [k for k, rep in linked_to_rep if rep in lost_reps and k not in lost]
            results.update(resolve_entities_bulk(db, redo, attributes_by_key, _depth + 1))

        for k in new_reps:
            if k in lost:
                continue
            sugg = [dict(s) for s in suggestions[k]]
            for s in sugg:
                if isinstance(s["resolved_entity_id"], tuple):
                    s["resolved_entity_id"] = ent_id[s["resolved_entity_id"][1]]
            results[k] = ResolutionResult(ent_id[k], 1.0, False, bool(sugg), sugg)
    return results


def resolve_entity(
    db: Session,
    tenant_id: str,
    entity_type: str,
    source: str,
    raw_value: str,
    attributes: Optional[dict] = None,
    commit: bool = True,
) -> ResolutionResult:
    """Single-value wrapper over resolve_entities_bulk (one code path)."""
    raw_value = (raw_value or "").strip()
    if not raw_value:
        raise ValueError("raw_value is required")
    key = (tenant_id, entity_type, source, raw_value)
    result = resolve_entities_bulk(db, [key], {key: attributes} if attributes else None)[key]
    if commit:
        db.commit()
    return result


def merge_entities(db: Session, tenant_id: str, keep_id: str, merge_id: str) -> ResolvedEntity:
    """
    Explicit human-confirmed merge (Section 13: 'allow corrections').
    Reassigns all aliases from merge_id onto keep_id, then deletes merge_id.
    Never called automatically — only from a human action via /merge.

    tenant_id is the CALLER's claimed tenant — both entities must belong to
    it, not just to each other. Checking only keep.tenant_id == merge.tenant_id
    (without pinning to the caller's tenant) would let anyone with the
    service API key merge entities belonging to any other tenant, as long
    as the two entities matched each other.
    """
    if keep_id == merge_id:
        raise ValueError("Cannot merge an entity into itself")
    keep = db.query(ResolvedEntity).filter(ResolvedEntity.id == keep_id, ResolvedEntity.tenant_id == tenant_id).first()
    merge = db.query(ResolvedEntity).filter(ResolvedEntity.id == merge_id, ResolvedEntity.tenant_id == tenant_id).first()
    if not keep or not merge:
        raise ValueError("Both entities must exist and belong to the specified tenant")
    if keep.entity_type != merge.entity_type:
        raise ValueError("Can only merge entities of the same type")

    # Iterate a snapshot, not the live collection — reassigning membership
    # while iterating merge.aliases would skip entries. Set the relationship
    # attribute (alias.entity = keep), not the raw FK column
    # (alias.entity_id = keep.id): setting only the column left the ORM's
    # in-memory relationship state still pointing at `merge`, so when
    # `merge` was deleted, SQLAlchemy's unit-of-work saw these aliases as
    # still belonging to the deleted parent and nulled their FK instead of
    # respecting the column we'd set — a real NotNullViolation caught by
    # testing the actual merge, not just the rejection path.
    for alias in list(merge.aliases):
        alias.entity = keep

    # BusinessEvent rows reference a ResolvedEntity directly via
    # resolved_customer_id/resolved_product_id/resolved_employee_id/
    # resolved_supplier_id (Section 6/13 — added when entity resolution was
    # wired into ingestion). Aliases aren't the only thing pointing at
    # `merge` — these FKs do too, and deleting merge without reassigning
    # them first violates the FK constraint (caught by actually running
    # the merge against real ingested events, not just against aliases).
    from app.models.core import BusinessEvent
    for col_name in ("resolved_customer_id", "resolved_product_id", "resolved_employee_id", "resolved_supplier_id"):
        db.query(BusinessEvent).filter(
            BusinessEvent.tenant_id == tenant_id, getattr(BusinessEvent, col_name) == merge_id
        ).update({col_name: keep_id})

    db.delete(merge)
    db.commit()
    db.refresh(keep)
    return keep
