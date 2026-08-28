from fastapi import APIRouter, HTTPException, Response, status

from app.core.config import settings
from app.core.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    HRUser,
    issue_hr_cookie,
    verify_hr_password,
)
from app.schemas import LoginRequest

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    if not verify_hr_password(payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect password")
    response.set_cookie(
        SESSION_COOKIE,
        issue_hr_cookie(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.app_env != "dev",
    )
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = HRUser) -> dict:
    return {"role": user.get("role", "hr"), "mock_ai": settings.mock_ai}
