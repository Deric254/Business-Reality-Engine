"""Targets the owner sets, how the business is doing against them, and why (Section: goals)."""
from decimal import Decimal
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from app.core import audit, goals as goals_core, movers
from app.core.auth_users import authorize_tenant
from app.core.db import get_db
from app.models.config import Goal

router = APIRouter()


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]       # stripped BEFORE the length check: blanks are rejected


class GoalIn(BaseModel):
    tenant_id: str
    name: Name
    metric: Literal["sales", "spending", "profit"]
    cadence: Literal["week", "month", "quarter", "year"]
    target_amount: Decimal = Field(gt=0, le=Decimal("1e13"), decimal_places=2)


class GoalPatch(BaseModel):
    name: Optional[Name] = None
    target_amount: Optional[Decimal] = Field(default=None, gt=0, le=Decimal("1e13"), decimal_places=2)


def _owned(db: Session, goal_id: str, tenant_id: str) -> Goal:
    g = db.query(Goal).filter_by(id=goal_id, tenant_id=tenant_id).first()
    if not g:
        raise HTTPException(404, "Target not found")
    return g


@router.get("")
def list_goals(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    authorize_tenant(authorization, tenant_id)
    return goals_core.all_progress(db, tenant_id)


@router.post("")
def create_goal(payload: GoalIn, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    if db.query(Goal).filter_by(tenant_id=payload.tenant_id).count() >= goals_core.MAX_GOALS:
        raise HTTPException(409, f"At most {goals_core.MAX_GOALS} targets per business")
    if db.query(Goal.id).filter(Goal.tenant_id == payload.tenant_id, Goal.name == payload.name.strip()).first():
        raise HTTPException(409, "A target with this name already exists")
    g = Goal(tenant_id=payload.tenant_id, name=payload.name.strip(), metric=payload.metric, cadence=payload.cadence, target_amount=payload.target_amount)
    db.add(g); db.commit(); db.refresh(g)
    audit.record(db, "goal.create", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id, target=g.id,
                 detail={"metric": g.metric, "cadence": g.cadence, "target": str(g.target_amount)})
    return goals_core.progress(db, g)


@router.patch("/{goal_id}")
def update_goal(goal_id: str, tenant_id: str, payload: GoalPatch, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    g = _owned(db, goal_id, tenant_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        changes["name"] = changes["name"].strip()
        if db.query(Goal.id).filter(Goal.tenant_id == tenant_id, Goal.name == changes["name"], Goal.id != g.id).first():
            raise HTTPException(409, "A target with this name already exists")
    for k, v in changes.items():
        if v is not None:
            setattr(g, k, v)
    db.commit(); db.refresh(g)
    audit.record(db, "goal.update", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=goal_id, detail=payload.model_dump(exclude_unset=True, mode="json"))
    return goals_core.progress(db, g)


@router.delete("/{goal_id}")
def delete_goal(goal_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    db.delete(_owned(db, goal_id, tenant_id)); db.commit()
    audit.record(db, "goal.delete", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=goal_id)
    return {"deleted": True}


@router.post("/{goal_id}/explain")
def explain_goal(goal_id: str, tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Why the business is ahead of or behind this target: exact figures by branch, product and spending category, in
    words from the connected AI when there is one (the figures are returned either way)."""
    authorize_tenant(authorization, tenant_id)
    return goals_core.explain(db, _owned(db, goal_id, tenant_id))


@router.get("/changes")
def changes(tenant_id: str, days: int = Query(30, ge=0, le=3650), db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """What improved and what got worse since the period before."""
    authorize_tenant(authorization, tenant_id)
    return movers.build(db, tenant_id, days)
