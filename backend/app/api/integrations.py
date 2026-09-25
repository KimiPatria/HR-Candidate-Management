"""What optional integrations this deployment has been given credentials for.

The HR frontend asks once at load and hides what is not configured, so an unconfigured
drive is an absent button rather than one that opens a picker and fails. Everything
returned is a public client identifier - see the block in `core/config.py` for why there
is no secret in this response and no token exchange on this server.
"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.security import HRUser
from app.schemas import DriveIntegration, IntegrationsOut

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("", response_model=IntegrationsOut)
async def get_integrations(user: dict = HRUser) -> IntegrationsOut:
    return IntegrationsOut(
        google_drive=DriveIntegration(
            enabled=settings.google_drive_enabled,
            client_id=settings.google_client_id,
            api_key=settings.google_api_key,
            app_id=settings.google_app_id,
        ),
        onedrive=DriveIntegration(
            enabled=settings.onedrive_enabled,
            client_id=settings.microsoft_client_id,
        ),
    )
