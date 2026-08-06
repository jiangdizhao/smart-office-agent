from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/api/admin/result-center",
    tags=["result-center-admin-auth"],
)

_PASSWORD_ENV = "SMART_OFFICE_ADMIN_PASSWORD"
_PASSWORD_HASH_ENV = "SMART_OFFICE_ADMIN_PASSWORD_HASH"
_TOKEN_TTL_ENV = "SMART_OFFICE_ADMIN_TOKEN_TTL_SECONDS"
_PBKDF2_ITERATIONS = 310_000
_MAX_FAILURES = 5
_FAILURE_WINDOW_SECONDS = 5 * 60
_LOCKOUT_SECONDS = 30
_CONTEXT_MAX_LENGTH = 180


@dataclass(frozen=True)
class AdminSession:
    token: str
    visit_id: str
    panel_instance_id: str
    expires_at: float


@dataclass
class FailureState:
    count: int
    first_failure_at: float
    locked_until: float = 0.0


class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    visit_id: str = Field(min_length=1, max_length=_CONTEXT_MAX_LENGTH)
    panel_instance_id: str = Field(min_length=1, max_length=_CONTEXT_MAX_LENGTH)


class AdminLoginResponse(BaseModel):
    ok: bool = True
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    visit_id: str
    panel_instance_id: str


_LOCK = threading.RLock()
_SESSIONS: dict[str, AdminSession] = {}
_FAILURES: dict[str, FailureState] = {}


def _now() -> float:
    return time.monotonic()


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return str(host or "unknown")


def _token_ttl_seconds() -> int:
    try:
        configured = int(os.getenv(_TOKEN_TTL_ENV, "600"))
    except ValueError:
        configured = 600
    return max(60, min(3600, configured))


def result_center_admin_configured() -> bool:
    return bool(
        os.getenv(_PASSWORD_HASH_ENV, "").strip()
        or os.getenv(_PASSWORD_ENV, "")
    )


def hash_admin_password(password: str, *, salt: bytes | None = None) -> str:
    clean = password.encode("utf-8")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        clean,
        actual_salt,
        _PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{actual_salt.hex()}${digest.hex()}"
    )


def _verify_hash(candidate: str, configured: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, digest_hex = configured.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        if iterations < 100_000 or iterations > 2_000_000:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (TypeError, ValueError):
        return False
    observed = hashlib.pbkdf2_hmac(
        "sha256",
        candidate.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(observed, expected)


def _verify_password(candidate: str) -> bool:
    configured_hash = os.getenv(_PASSWORD_HASH_ENV, "").strip()
    if configured_hash:
        return _verify_hash(candidate, configured_hash)
    configured_password = os.getenv(_PASSWORD_ENV, "")
    if not configured_password:
        return False
    return hmac.compare_digest(
        candidate.encode("utf-8"),
        configured_password.encode("utf-8"),
    )


def _purge_expired_locked(now: float) -> None:
    expired_tokens = [
        token
        for token, session in _SESSIONS.items()
        if session.expires_at <= now
    ]
    for token in expired_tokens:
        _SESSIONS.pop(token, None)
    expired_failures = [
        key
        for key, failure in _FAILURES.items()
        if failure.locked_until <= now
        and now - failure.first_failure_at > _FAILURE_WINDOW_SECONDS
    ]
    for key in expired_failures:
        _FAILURES.pop(key, None)


def _bearer_token(authorization: str | None) -> str | None:
    clean = str(authorization or "").strip()
    if not clean:
        return None
    scheme, separator, token = clean.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        return None
    return token.strip() or None


def _clean_context(value: object) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > _CONTEXT_MAX_LENGTH:
        return ""
    return clean


def _request_context(request: Request) -> tuple[str, str]:
    visit_id = _clean_context(
        request.headers.get("x-smartoffice-visit-id")
        or request.query_params.get("visit_id")
    )
    panel_instance_id = _clean_context(
        request.headers.get("x-smartoffice-panel-instance-id")
        or request.query_params.get("panel_instance_id")
    )
    return visit_id, panel_instance_id


def _lookup_session(
    request: Request,
    authorization: str | None,
    access_token: str | None,
) -> AdminSession | None:
    now = _now()
    token = _bearer_token(authorization) or str(access_token or "").strip() or None
    visit_id, panel_instance_id = _request_context(request)
    if not token or not visit_id or not panel_instance_id:
        return None
    with _LOCK:
        _purge_expired_locked(now)
        session = _SESSIONS.get(token)
        if (
            session
            and session.expires_at > now
            and hmac.compare_digest(session.visit_id, visit_id)
            and hmac.compare_digest(session.panel_instance_id, panel_instance_id)
        ):
            return session
    return None


def require_result_center_admin(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query()] = None,
) -> AdminSession:
    session = _lookup_session(request, authorization, access_token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Administrator authentication for the current Visit and result-center "
                "panel instance is required."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return session


def _record_failure(client_key: str, now: float) -> int:
    with _LOCK:
        failure = _FAILURES.get(client_key)
        if failure is None or now - failure.first_failure_at > _FAILURE_WINDOW_SECONDS:
            failure = FailureState(count=0, first_failure_at=now)
        failure.count += 1
        if failure.count >= _MAX_FAILURES:
            failure.locked_until = now + _LOCKOUT_SECONDS
        _FAILURES[client_key] = failure
        return max(0, int(failure.locked_until - now))


def _locked_seconds(client_key: str, now: float) -> int:
    with _LOCK:
        _purge_expired_locked(now)
        failure = _FAILURES.get(client_key)
        if not failure or failure.locked_until <= now:
            return 0
        return max(1, int(failure.locked_until - now))


@router.post("/login", response_model=AdminLoginResponse)
def login_result_center_admin(
    payload: AdminLoginRequest,
    request: Request,
) -> AdminLoginResponse:
    if not result_center_admin_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Result-center administrator password is not configured. Set "
                "SMART_OFFICE_ADMIN_PASSWORD or SMART_OFFICE_ADMIN_PASSWORD_HASH "
                "before starting the Backend."
            ),
        )

    visit_id = _clean_context(payload.visit_id)
    panel_instance_id = _clean_context(payload.panel_instance_id)
    if not visit_id or not panel_instance_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A valid Visit and panel instance are required.",
        )

    now = _now()
    client_key = _client_key(request)
    locked = _locked_seconds(client_key, now)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many incorrect attempts. Try again in {locked} seconds.",
            headers={"Retry-After": str(locked)},
        )

    if not _verify_password(payload.password):
        retry_after = _record_failure(client_key, now)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(
            status_code=(
                status.HTTP_429_TOO_MANY_REQUESTS
                if retry_after
                else status.HTTP_401_UNAUTHORIZED
            ),
            detail=(
                f"Too many incorrect attempts. Try again in {retry_after} seconds."
                if retry_after
                else "Administrator password is incorrect."
            ),
            headers=headers,
        )

    ttl = _token_ttl_seconds()
    token = secrets.token_urlsafe(32)
    session = AdminSession(
        token=token,
        visit_id=visit_id,
        panel_instance_id=panel_instance_id,
        expires_at=now + ttl,
    )
    with _LOCK:
        _FAILURES.pop(client_key, None)
        _SESSIONS[token] = session
    return AdminLoginResponse(
        access_token=token,
        expires_in_seconds=ttl,
        visit_id=visit_id,
        panel_instance_id=panel_instance_id,
    )


@router.get("/status")
def result_center_admin_status(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query()] = None,
) -> dict[str, object]:
    session = _lookup_session(request, authorization, access_token)
    now = _now()
    visit_id, panel_instance_id = _request_context(request)
    return {
        "ok": True,
        "configured": result_center_admin_configured(),
        "authenticated": session is not None,
        "visit_id": visit_id or None,
        "panel_instance_id": panel_instance_id or None,
        "expires_in_seconds": (
            max(0, int(session.expires_at - now)) if session else 0
        ),
    }


@router.post("/logout")
def logout_result_center_admin(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query()] = None,
) -> dict[str, bool]:
    token = _bearer_token(authorization) or str(access_token or "").strip() or None
    visit_id, panel_instance_id = _request_context(request)
    removed = False
    if token:
        with _LOCK:
            session = _SESSIONS.get(token)
            if session and (
                not visit_id
                or not panel_instance_id
                or (
                    hmac.compare_digest(session.visit_id, visit_id)
                    and hmac.compare_digest(session.panel_instance_id, panel_instance_id)
                )
            ):
                _SESSIONS.pop(token, None)
                removed = True
    return {"ok": True, "logged_out": removed}


def reset_result_center_auth_for_tests() -> None:
    with _LOCK:
        _SESSIONS.clear()
        _FAILURES.clear()
