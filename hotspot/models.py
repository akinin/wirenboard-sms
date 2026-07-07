from typing import Optional

from pydantic import BaseModel, Field, field_validator

from api.models import normalize_phone


class HotspotOtpRequest(BaseModel):
    phone: str
    client_mac: str = Field(min_length=8, max_length=32)
    ap_mac: Optional[str] = Field(default=None, max_length=32)
    redirect_url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)


class HotspotOtpVerifyRequest(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=10)
    client_mac: str = Field(min_length=8, max_length=32)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)
