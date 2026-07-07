from functools import lru_cache
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_token: Optional[SecretStr] = None
    app_secret: SecretStr = Field(default=SecretStr("change-me"))
    app_host: str = "0.0.0.0"
    app_port: int = 8088
    database_path: str = "./data/sms_gateway.sqlite3"

    wb_mqtt_host: str = "127.0.0.1"
    wb_mqtt_port: int = 1883
    wb_mqtt_username: Optional[str] = None
    wb_mqtt_password: Optional[SecretStr] = None
    wb_sms_topic: str = "/devices/sms_sender/controls/send/on"

    sms_backend: str = "mqtt"
    mmcli_modem_id: str = "any"

    unifi_base_url: Optional[str] = None
    unifi_username: Optional[str] = None
    unifi_password: Optional[SecretStr] = None
    unifi_site: str = "default"
    unifi_verify_tls: bool = False
    unifi_auth_minutes: int = 1440

    hotspot_access_log_path: str = "./data/hotspot_access.csv"
    telegram_bot_token: Optional[SecretStr] = None
    telegram_chat_id: Optional[str] = None

    otp_ttl_seconds: int = 300
    otp_length: int = 6
    otp_resend_seconds: int = 60
    otp_max_attempts: int = 5
    otp_message_template: str = "Your Wi-Fi code: {code}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
