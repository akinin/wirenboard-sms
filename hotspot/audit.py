import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

from api.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccessEvent:
    authorized_at: datetime
    expires_at: datetime
    client_mac: str
    phone: str


def build_access_event(
    settings: Settings,
    client_mac: str,
    phone: str,
    authorized_at_ts: Optional[int] = None,
    valid_until_ts: Optional[int] = None,
) -> AccessEvent:
    authorized_at = (
        datetime.fromtimestamp(int(authorized_at_ts))
        if authorized_at_ts
        else datetime.now()
    )
    return AccessEvent(
        authorized_at=authorized_at,
        expires_at=(
            datetime.fromtimestamp(int(valid_until_ts))
            if valid_until_ts
            else authorized_at + timedelta(minutes=settings.unifi_auth_minutes)
        ),
        client_mac=client_mac.lower(),
        phone=phone,
    )


def record_access_event(settings: Settings, event: AccessEvent) -> None:
    _append_access_log(settings, event)
    _send_telegram_notification(settings, event)


def _append_access_log(settings: Settings, event: AccessEvent) -> None:
    path = Path(settings.hotspot_access_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if needs_header:
            writer.writerow(["date", "time", "mac", "phone", "valid_until"])
        writer.writerow(
            [
                event.authorized_at.strftime("%Y-%m-%d"),
                event.authorized_at.strftime("%H:%M:%S"),
                event.client_mac,
                event.phone,
                event.expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )


def _send_telegram_notification(settings: Settings, event: AccessEvent) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    token = settings.telegram_bot_token.get_secret_value()
    text = (
        "🛜 Wi-Fi: Olshaniki Guest\n\n"
        f"📱Phone: {event.phone}\n"
        f"🌐MAC: {event.client_mac}\n\n"
        f"Authorized: {event.authorized_at:%d.%m.%Y %H:%M:%S}\n"
        f"Valid until: {event.expires_at:%d.%m.%Y %H:%M:%S}\n\n"
        "#wifi_guest"
    )
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("failed to send Telegram hotspot notification: %s", exc)
