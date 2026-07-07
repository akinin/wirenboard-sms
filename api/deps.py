from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings
from .otp import OtpService
from .sms import SmsSender
from .store import Store


def get_store(settings: Settings = Depends(get_settings)) -> Store:
    return Store(settings.database_path)


def require_api_token(
    settings: Settings = Depends(get_settings),
    authorization: Optional[str] = Header(default=None),
) -> None:
    expected = settings.api_token.get_secret_value() if settings.api_token else None
    if not expected:
        return
    if authorization not in (expected, f"Bearer {expected}"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


def get_otp_service(
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
) -> OtpService:
    return OtpService(settings=settings, store=store, sms_sender=SmsSender(settings))
