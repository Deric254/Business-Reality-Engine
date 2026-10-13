"""
User management (Section 48/50). Owner-only, tenant-scoped.

An owner either invites a colleague by email (a single-use link, valid 7 days; needs SMTP) or, when
mail is not set up, creates the account and hands over a one-time temporary password (shown once, never stored in plain text). That account can do nothing
except change its password (enforced server-side via a token claim) until it does. Guards: a business
always keeps at least one owner; nobody removes or demotes themselves; every change revokes the
affected user's sessions and is written to the audit log.
"""
import secrets
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core import audit, recovery, revocation
from app.core.auth_users import authorize_tenant, hash_password
from app.core.db import get_db
from app.models.core import Tenant, User

router = APIRouter()
MAX_USERS_PER_TENANT = 50


def _temp_password() -> str:
    # 14 URL-safe chars (~84 bits); ambiguous characters are fine, it is copied, not typed from memory
    return secrets.token_urlsafe(11)[:14]


class UserOut(BaseModel):
    id: str
    email: str
    role: str
    created_at: Optional[datetime]
    must_change_password: bool
    is_me: bool = False


class UserCreate(BaseModel):
    tenant_id: str
    email: EmailStr
    role: Literal["owner", "staff"] = "staff"


class UserCreated(UserOut):
    temporary_password: str


class RoleUpdate(BaseModel):
    role: Literal["owner", "staff"]


def _out(u: User, me_id: str) -> UserOut:
    return UserOut(id=u.id, email=u.email, role=u.role, created_at=u.created_at,
                   must_change_password=bool(u.must_change_password), is_me=u.id == me_id)


def _owned(db: Session, user_id: str, tenant_id: str) -> User:
    u = db.query(User).filter(User.id == user_id, User.tenant_id == tenant_id).first()
    if not u:
        raise HTTPException(404, "User not found")          # same answer for another tenant's user
    return u


def _owner_count(db: Session, tenant_id: str) -> int:
    return db.query(User).filter(User.tenant_id == tenant_id, User.role == "owner").count()


@router.get("/", response_model=list[UserOut])
def list_users(tenant_id: str, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    rows = db.query(User).filter(User.tenant_id == tenant_id).order_by(User.created_at).all()
    return [_out(u, me.user_id) for u in rows]


@router.post("/", response_model=UserCreated)
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db),
                authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    if db.query(User).filter(User.tenant_id == payload.tenant_id).count() >= MAX_USERS_PER_TENANT:
        raise HTTPException(409, f"At most {MAX_USERS_PER_TENANT} users per business")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(409, "Could not create this account")     # generic: no email enumeration
    temp = _temp_password()
    u = User(tenant_id=payload.tenant_id, email=payload.email, password_hash=hash_password(temp),
             role=payload.role, must_change_password=True)
    db.add(u)
    db.commit(); db.refresh(u)
    audit.record(db, "user.create", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id,
                 target=u.id, detail={"email": u.email, "role": u.role})
    return UserCreated(**_out(u, me.user_id).model_dump(), temporary_password=temp)


class InviteRequest(BaseModel):
    tenant_id: str
    email: EmailStr
    role: Literal["owner", "staff"] = "staff"


@router.post("/invite", response_model=UserOut)
def invite_user(payload: InviteRequest, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    if not recovery.mail_enabled():
        raise HTTPException(409, "Email is not configured on this server; create the user with a temporary password instead")
    if db.query(User).filter(User.tenant_id == payload.tenant_id).count() >= MAX_USERS_PER_TENANT:
        raise HTTPException(409, f"At most {MAX_USERS_PER_TENANT} users per business")
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(409, "Could not create this account")
    tenant = db.query(Tenant).filter(Tenant.id == payload.tenant_id).first()
    # Unusable placeholder password: the account cannot sign in until the invitation is accepted.
    u = User(tenant_id=payload.tenant_id, email=payload.email, password_hash=hash_password(secrets.token_urlsafe(24)),
             role=payload.role, must_change_password=True)
    db.add(u)
    db.commit(); db.refresh(u)
    token = recovery.issue_token(db, u, "invite")
    background.add_task(recovery.send_invite_email, u.email, token, tenant.name if tenant else "your business")
    audit.record(db, "user.invite", request=request, tenant_id=payload.tenant_id, actor_user_id=me.user_id,
                 target=u.id, detail={"email": u.email, "role": u.role})
    return _out(u, me.user_id)


@router.post("/{user_id}/resend-invite")
def resend_invite(user_id: str, tenant_id: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                  authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    u = _owned(db, user_id, tenant_id)
    if not recovery.mail_enabled():
        raise HTTPException(409, "Email is not configured on this server")
    if not u.must_change_password:
        raise HTTPException(400, "This user has already set a password")
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    token = recovery.issue_token(db, u, "invite")
    background.add_task(recovery.send_invite_email, u.email, token, tenant.name if tenant else "your business")
    audit.record(db, "user.resend_invite", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=u.id)
    return {"ok": True}


@router.patch("/{user_id}", response_model=UserOut)
def update_role(user_id: str, tenant_id: str, payload: RoleUpdate, request: Request, db: Session = Depends(get_db),
                authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    u = _owned(db, user_id, tenant_id)
    if u.id == me.user_id:
        raise HTTPException(400, "You cannot change your own role")
    if u.role == "owner" and payload.role != "owner" and _owner_count(db, tenant_id) <= 1:
        raise HTTPException(400, "A business must keep at least one owner")
    if u.role != payload.role:
        before = u.role
        u.role = payload.role
        revocation.revoke_user_tokens(db, u.id)                 # their token still carries the old role
        db.commit()
        audit.record(db, "user.role_change", request=request, tenant_id=tenant_id, actor_user_id=me.user_id,
                     target=u.id, detail={"from": before, "to": u.role})
    return _out(u, me.user_id)


@router.post("/{user_id}/reset-password", response_model=UserCreated)
def reset_password(user_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
                   authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    u = _owned(db, user_id, tenant_id)
    if u.id == me.user_id:
        raise HTTPException(400, "Use change password for your own account")
    temp = _temp_password()
    u.password_hash = hash_password(temp)
    u.must_change_password = True
    revocation.revoke_user_tokens(db, u.id)
    db.commit()
    audit.record(db, "user.reset_password", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=u.id)
    return UserCreated(**_out(u, me.user_id).model_dump(), temporary_password=temp)


@router.delete("/{user_id}")
def delete_user(user_id: str, tenant_id: str, request: Request, db: Session = Depends(get_db),
                authorization: Optional[str] = Header(default=None)):
    me = authorize_tenant(authorization, tenant_id, require_role="owner")
    u = _owned(db, user_id, tenant_id)
    if u.id == me.user_id:
        raise HTTPException(400, "You cannot remove yourself")
    if u.role == "owner" and _owner_count(db, tenant_id) <= 1:
        raise HTTPException(400, "A business must keep at least one owner")
    email = u.email
    revocation.revoke_user_tokens(db, u.id)       # their access token must stop working now, not in 30 minutes
    db.delete(u)
    db.commit()
    audit.record(db, "user.delete", request=request, tenant_id=tenant_id, actor_user_id=me.user_id, target=user_id,
                 detail={"email": email})
    return {"deleted": True}
