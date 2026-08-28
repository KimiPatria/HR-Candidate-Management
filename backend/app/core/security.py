import secrets

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.core.config import settings

SESSION_COOKIE = "hr_session"
SESSION_MAX_AGE = 60 * 60 * 12  # 12 hours

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="hr-auth")


def hr_cookie_serializer() -> URLSafeTimedSerializer:
    """Exposed so the WebSocket handler can verify the same cookie outside a request."""
    return _serializer


def issue_hr_cookie() -> str:
    return _serializer.dumps({"role": "hr"})


def verify_hr_password(candidate: str) -> bool:
    # Constant-time compare so the shared password can't be timed out character by character.
    return secrets.compare_digest(candidate, settings.hr_password)


def require_hr(request: Request) -> dict:
    """Dependency guarding every HR-only route."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "HR login required")
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session invalid or expired")


HRUser = Depends(require_hr)


def new_join_token() -> str:
    return secrets.token_urlsafe(24)
