import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from .config import Settings
from .sms import SmsSender
from .store import Store


@dataclass
class OtpService:
    settings: Settings
    store: Store
    sms_sender: SmsSender

    def request_code(self, phone: str, purpose: str) -> None:
        existing = self.store.get_otp(phone, purpose)
        now = int(time.time())
        if existing and now - int(existing["sent_at"]) < self.settings.otp_resend_seconds:
            raise ValueError("code was sent recently")

        code = self._generate_code()
        self.store.save_otp(phone, purpose, self._hash_code(phone, purpose, code), self.settings.otp_ttl_seconds)
        message = self.settings.otp_message_template.format(code=code).replace("\\n", "\n")
        self.sms_sender.send(phone, message)

    def verify_code(self, phone: str, purpose: str, code: str) -> bool:
        row = self.store.get_otp(phone, purpose)
        now = int(time.time())
        if not row or row["verified_at"] is not None:
            return False
        if now > int(row["expires_at"]):
            return False
        if int(row["attempts"]) >= self.settings.otp_max_attempts:
            return False

        self.store.increment_attempts(phone, purpose)
        expected_hash = str(row["code_hash"])
        if not hmac.compare_digest(expected_hash, self._hash_code(phone, purpose, code)):
            return False

        self.store.mark_verified(phone, purpose)
        return True

    def _generate_code(self) -> str:
        maximum = 10**self.settings.otp_length
        return str(secrets.randbelow(maximum)).zfill(self.settings.otp_length)

    def _hash_code(self, phone: str, purpose: str, code: str) -> str:
        secret = self.settings.app_secret.get_secret_value().encode()
        value = f"{phone}:{purpose}:{code}".encode()
        return hmac.new(secret, value, hashlib.sha256).hexdigest()
