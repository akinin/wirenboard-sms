from fastapi import Depends, FastAPI, HTTPException

from .config import Settings, get_settings
from .deps import get_otp_service, require_api_token
from .models import (
    OtpRequest,
    OtpRequestResponse,
    OtpVerifyRequest,
    OtpVerifyResponse,
    SmsRequest,
    SmsResponse,
)
from .otp import OtpService
from .sms import SmsSender
from hotspot.routes import router as hotspot_router

app = FastAPI(title="SMS Gateway API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/sms", response_model=SmsResponse, dependencies=[Depends(require_api_token)])
def send_sms(
    payload: SmsRequest,
    settings: Settings = Depends(get_settings),
) -> SmsResponse:
    try:
        SmsSender(settings).send(payload.phone, payload.message)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SmsResponse(ok=True)


@app.post("/api/otp/request", response_model=OtpRequestResponse, dependencies=[Depends(require_api_token)])
def request_otp(payload: OtpRequest, service: OtpService = Depends(get_otp_service)) -> OtpRequestResponse:
    try:
        service.request_code(payload.phone, payload.purpose)
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OtpRequestResponse(
        ok=True,
        ttl_seconds=service.settings.otp_ttl_seconds,
        resend_after_seconds=service.settings.otp_resend_seconds,
    )


@app.post("/api/otp/verify", response_model=OtpVerifyResponse, dependencies=[Depends(require_api_token)])
def verify_otp(payload: OtpVerifyRequest, service: OtpService = Depends(get_otp_service)) -> OtpVerifyResponse:
    verified = service.verify_code(payload.phone, payload.purpose, payload.code)
    return OtpVerifyResponse(ok=True, verified=verified)


app.include_router(hotspot_router)
