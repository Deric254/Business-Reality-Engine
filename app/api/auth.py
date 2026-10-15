import time
from typing import Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.auth_users import (
    hash_password, verify_password, create_access_token, create_refresh_token, decode_token,
    authorize_tenant, get_current_user, MAX_PASSWORD_BYTES, CurrentUser,
)
from app.core import audit, lockout, recovery, revocation, security_questions as sq
from app.core.auth import require_admin_key
from app.core.rate_limit import auth_limiter, _client_ip

from app.models.core import User, Tenant

router = APIRouter()


class RegisterRequest(BaseModel):
    tenant_id: str
    email: EmailStr
    password: str
    role: str = "staff"  # first user for a tenant should register as "owner" explicitly


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


def _check_new_password(pw: str) -> None:
    if len(pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if len(pw.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(400, "Password must be at most 72 bytes")


def _tokens(user: User) -> TokenPair:
    mc = bool(user.must_change_password)
    return TokenPair(
        access_token=create_access_token(user.id, user.tenant_id, user.role, mc),
        refresh_token=create_refresh_token(user.id, user.tenant_id, user.role, mc),
        must_change_password=mc,
    )


def _actor_id(authorization: Optional[str]) -> Optional[str]:
    try:
        return decode_token(authorization.removeprefix("Bearer "), expected_type="access").user_id if authorization else None
    except HTTPException:
        return None


def _throttle(request: Request):
    """Credential-guessing protection: 10 attempts/minute per source IP on
    login/refresh/register, independent of the general 300/min limit."""
    allowed, retry_after = auth_limiter.check(_client_ip(request))
    if not allowed:
        raise HTTPException(429, "Too many attempts", headers={"Retry-After": str(retry_after)})


@router.post("/register", response_model=TokenPair)
def register(
    payload: RegisterRequest, request: Request, db: Session = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
    x_admin_key: Optional[str] = Header(default=None),
):
    """Creating accounts is privileged: either the deployment admin key
    (bootstrap the first owner of a tenant) or an OWNER of that same tenant."""
    _throttle(request)
    if x_admin_key:
        require_admin_key(x_admin_key)
    else:
        authorize_tenant(authorization, payload.tenant_id, require_role="owner")
    if payload.role not in ("owner", "staff"):
        raise HTTPException(400, "role must be 'owner' or 'staff'")

    tenant = db.query(Tenant).filter(Tenant.id == payload.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        # Generic message — don't confirm which emails are already registered.
        raise HTTPException(409, "Could not register this account")

    _check_new_password(payload.password)

    user = User(
        tenant_id=payload.tenant_id, email=payload.email,
        password_hash=hash_password(payload.password), role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    audit.record(db, "auth.register", request=request, tenant_id=user.tenant_id, actor_type="admin" if x_admin_key else "user",
                 actor_user_id=None if x_admin_key else _actor_id(authorization), target=user.id, detail={"role": user.role})

    return _tokens(user)


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    _throttle(request)
    email = str(payload.email)
    user = db.query(User).filter(User.email == payload.email).first()
    try:
        lockout.check_not_locked(email)
    except HTTPException:
        audit.record(db, "auth.login_blocked", request=request, tenant_id=user.tenant_id if user else None,
                     actor_user_id=user.id if user else None, actor_type="anonymous", success=False, detail={"email": email})
        raise
    # Same error whether the email doesn't exist or the password is wrong —
    # distinguishing them tells an attacker which emails are registered.
    if not user or not verify_password(payload.password, user.password_hash):
        locked = lockout.record_failure(email)
        audit.record(db, "auth.login_failed", request=request, tenant_id=user.tenant_id if user else None,
                     actor_user_id=user.id if user else None, actor_type="anonymous", success=False,
                     detail={"email": email, "locked": locked})
        raise HTTPException(401, "Invalid email or password")
    lockout.record_success(email)
    audit.record(db, "auth.login", request=request, tenant_id=user.tenant_id, actor_user_id=user.id)
    return _tokens(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """Refresh tokens are SINGLE-USE: each call retires the presented token and issues a new pair.
    Presenting a token that was already used means it was copied, so every token of that user is revoked."""
    _throttle(request)
    current = decode_token(payload.refresh_token, expected_type="refresh")
    user = db.query(User).filter(User.id == current.user_id).first()
    if not user:
        raise HTTPException(401, "User no longer exists")
    if current.iat_ms <= int(user.tokens_valid_after_ms or 0):
        raise HTTPException(401, "Token has been revoked")
    if not revocation.consume_refresh_token(db, current.jti, user.id, current.exp):
        revocation.revoke_user_tokens(db, user.id)
        db.commit()
        audit.record(db, "auth.refresh_reuse_detected", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, success=False)
        raise HTTPException(401, "Token has been revoked")
    db.commit()                      # the used-token record is in the database; replays are caught by consume_refresh_token
    return _tokens(user)


@router.post("/logout")
def logout(payload: LogoutRequest, request: Request, db: Session = Depends(get_db),
           authorization: Optional[str] = Header(default=None)):
    """Revokes the presented access token (and the refresh token, if sent). Always succeeds for a valid access token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    me = decode_token(authorization.removeprefix("Bearer "), expected_type="access")
    revocation.revoke_token(db, me.jti, me.user_id, me.exp)
    if payload.refresh_token:
        try:
            r = decode_token(payload.refresh_token, expected_type="refresh")
            if r.user_id == me.user_id:                # never revoke somebody else's token
                revocation.revoke_token(db, r.jti, r.user_id, r.exp)
        except HTTPException:
            pass                                       # an already-dead refresh token needs no revoking
    db.commit()
    audit.record(db, "auth.logout", request=request, tenant_id=me.tenant_id, actor_user_id=me.user_id)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(request: Request, db: Session = Depends(get_db), authorization: Optional[str] = Header(default=None)):
    """Sign out everywhere: every token issued to this user up to now stops working."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    me = decode_token(authorization.removeprefix("Bearer "), expected_type="access")
    revocation.revoke_user_tokens(db, me.user_id)
    db.commit()
    audit.record(db, "auth.logout_all", request=request, tenant_id=me.tenant_id, actor_user_id=me.user_id)
    return {"ok": True}


@router.post("/change-password", response_model=TokenPair)
def change_password(payload: ChangePasswordRequest, request: Request, db: Session = Depends(get_db),
                    authorization: Optional[str] = Header(default=None)):
    """Needs the current password even with a valid token (a stolen session must not be able to take over the
    account). Revokes every existing token and returns a fresh pair."""
    _throttle(request)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Authentication required")
    me = decode_token(authorization.removeprefix("Bearer "), expected_type="access")
    user = db.query(User).filter(User.id == me.user_id).first()
    if not user:
        raise HTTPException(401, "User no longer exists")
    lockout.check_not_locked(user.email)
    if not verify_password(payload.current_password, user.password_hash):
        locked = lockout.record_failure(user.email)
        audit.record(db, "auth.change_password_failed", request=request, tenant_id=user.tenant_id, actor_user_id=user.id,
                     success=False, detail={"locked": locked})
        raise HTTPException(401, "Current password is incorrect")
    _check_new_password(payload.new_password)
    if payload.new_password == payload.current_password:
        raise HTTPException(400, "New password must differ from the current one")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    revocation.revoke_user_tokens(db, user.id)
    db.commit()
    lockout.record_success(user.email)
    audit.record(db, "auth.change_password", request=request, tenant_id=user.tenant_id, actor_user_id=user.id)
    time.sleep(0.002)               # guarantee the new tokens' issue time is strictly after the revocation cutoff
    return _tokens(user)


class ForgotRequest(BaseModel):
    email: EmailStr


class ResetRequest(BaseModel):
    token: str
    new_password: str


@router.post("/forgot-password")
def forgot_password(payload: ForgotRequest, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Answers the same way whether or not the address is registered. `email_enabled` is a property of the
    server, not of the account, so it leaks nothing; it lets the page say so when no mail can be sent."""
    _throttle(request)
    email_enabled = recovery.mail_enabled()
    user = db.query(User).filter(User.email == payload.email).first()
    if user and email_enabled and recovery.email_allowed(payload.email):
        token = recovery.issue_token(db, user, "reset")
        background.add_task(recovery.send_reset_email, user.email, token)
        audit.record(db, "auth.forgot_password", request=request, tenant_id=user.tenant_id, target=user.id)
    return {"ok": True, "email_enabled": email_enabled, "message": "If that address has an account, a reset link is on its way."}


@router.post("/reset-password")
def reset_password(payload: ResetRequest, request: Request, db: Session = Depends(get_db)):
    """Completes a reset or an invitation. The token is spent only if the new password is acceptable,
    so a typo does not burn the link."""
    _throttle(request)
    _check_new_password(payload.new_password)
    spent = recovery.consume_token(db, payload.token)
    if not spent:
        raise HTTPException(400, "This link is invalid or has expired")
    user_id, purpose = spent
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(400, "This link is invalid or has expired")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    revocation.revoke_user_tokens(db, user.id)
    db.commit()
    lockout.record_success(user.email)
    audit.record(db, "auth.reset_password" if purpose == "reset" else "auth.accept_invite", request=request,
                 tenant_id=user.tenant_id, actor_user_id=user.id, target=user.id)
    return {"ok": True}


class QuestionAnswer(BaseModel):
    question: str
    answer: str


class SecurityQuestionsRequest(BaseModel):
    current_password: str
    items: list[QuestionAnswer]


class RecoveryEmail(BaseModel):
    email: EmailStr


class RecoverRequest(BaseModel):
    email: EmailStr
    answers: list[str]
    new_password: str


@router.get("/security-questions")
def get_security_questions(me: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """The list to choose from, and which questions this account has chosen (never the answers)."""
    user = db.query(User).filter(User.id == me.user_id).first()
    chosen = [row["q"] for row in (user.security_questions or [])] if user else []
    return {"options": sq.QUESTIONS, "count": sq.QUESTION_COUNT, "questions": chosen}


@router.put("/security-questions")
def set_security_questions(payload: SecurityQuestionsRequest, request: Request, me: CurrentUser = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    """Needs the current password, like a password change: a stolen session must not be able to rewrite recovery."""
    _throttle(request)
    user = db.query(User).filter(User.id == me.user_id).first()
    if not user:
        raise HTTPException(401, "User no longer exists")
    lockout.check_not_locked(user.email)
    if not verify_password(payload.current_password, user.password_hash):
        locked = lockout.record_failure(user.email)
        audit.record(db, "auth.security_questions_failed", request=request, tenant_id=user.tenant_id, actor_user_id=user.id,
                     success=False, detail={"locked": locked})
        raise HTTPException(401, "Current password is incorrect")
    items = [(i.question, i.answer) for i in payload.items]
    problem = sq.validate_choice(items)
    if problem:
        raise HTTPException(400, problem)
    user.security_questions = sq.hash_answers(items)
    db.commit()
    lockout.record_success(user.email)
    audit.record(db, "auth.security_questions_set", request=request, tenant_id=user.tenant_id, actor_user_id=user.id)
    return {"questions": [q for q, _ in items]}


@router.post("/recovery-questions")
def recovery_questions(payload: RecoveryEmail, request: Request, db: Session = Depends(get_db)):
    """The questions to answer. An unknown email, or one with no questions set, gets the same kind of reply
    (a repeatable decoy), so this cannot be used to find out who has an account."""
    _throttle(request)
    user = db.query(User).filter(User.email == payload.email).first()
    if user and user.security_questions and len(user.security_questions) == sq.QUESTION_COUNT:
        return {"questions": [row["q"] for row in user.security_questions]}
    return {"questions": sq.decoy_questions(payload.email)}


@router.post("/recover-with-answers")
def recover_with_answers(payload: RecoverRequest, request: Request, db: Session = Depends(get_db)):
    """Sets a new password when every security answer is right. Failures are locked per email, separately from sign-in."""
    _throttle(request)
    _check_new_password(payload.new_password)
    lock_key = f"recovery:{payload.email}"
    lockout.check_not_locked(lock_key)
    user = db.query(User).filter(User.email == payload.email).first()
    if not sq.answers_match(user.security_questions if user else None, payload.answers):
        locked = lockout.record_failure(lock_key)
        if user:
            audit.record(db, "auth.recover_with_answers_failed", request=request, tenant_id=user.tenant_id, target=user.id,
                         success=False, detail={"locked": locked})
        raise HTTPException(401, "Those answers don't match, or this account has no security questions set.")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    revocation.revoke_user_tokens(db, user.id)
    db.commit()
    lockout.record_success(lock_key)
    lockout.record_success(user.email)
    audit.record(db, "auth.recover_with_answers", request=request, tenant_id=user.tenant_id, actor_user_id=user.id, target=user.id)
    return {"ok": True}
