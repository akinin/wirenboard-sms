import re

from pydantic import BaseModel, Field, field_validator


_PHONE_SEPARATORS = re.compile(r"[\s\-().]+")


def normalize_phone(value: str) -> str:
    phone = _PHONE_SEPARATORS.sub("", value.strip())
    if phone.startswith("+7") and phone[1:].isdigit() and len(phone) == 12:
        return phone
    if phone.isdigit() and len(phone) == 10:
        return "+7" + phone
    if phone.startswith("8") and phone.isdigit() and len(phone) == 11:
        return "+7" + phone[1:]
    if not phone.startswith("+"):
        raise ValueError("phone must be in international format, for example +79991234567")
    if not phone[1:].isdigit() or len(phone) < 8 or len(phone) > 16:
        raise ValueError("phone has invalid format")
    return phone


class SmsRequest(BaseModel):
    phone: str
    message: str = Field(min_length=1, max_length=1000)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)


class SmsResponse(BaseModel):
    ok: bool


class OtpRequest(BaseModel):
    phone: str
    purpose: str = Field(default="default", min_length=1, max_length=64)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)


class OtpVerifyRequest(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=10)
    purpose: str = Field(default="default", min_length=1, max_length=64)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)


class OtpRequestResponse(BaseModel):
    ok: bool
    ttl_seconds: int
    resend_after_seconds: int


class OtpVerifyResponse(BaseModel):
    ok: bool
    verified: bool
