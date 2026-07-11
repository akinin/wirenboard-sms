import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from api.config import Settings, get_settings
from api.deps import get_otp_service, get_store, require_api_token
from api.models import OtpRequestResponse, OtpVerifyResponse
from api.otp import OtpService
from api.store import Store

from .models import HotspotOtpRequest, HotspotOtpVerifyRequest
from .audit import build_access_event, record_access_event
from .store import HotspotStore
from .unifi import UniFiClient

router = APIRouter()
LOGO_PATH = Path(__file__).with_name("assets") / "ahs.png"

PORTAL_TEXT = {
    "en": {
        "wifi_login": "Wi-Fi login", "phone": "Phone", "get_code": "Get SMS code",
        "sending": "Sending…", "sms_code": "SMS code", "enter_code": "Enter the SMS code",
        "code": "Code", "connect": "Connect", "invalid_code": "Invalid code. Try again.",
        "already_sent": "A code was already sent. Enter it below.", "done": "Done",
        "access_open": "Access granted", "access_already_open": "Access is already granted",
        "wifi_authorized": "Wi-Fi authorized.", "authorized_at": "Authorized at",
        "valid_until": "Valid until", "error": "Error", "blocked": "This client is blocked.",
        "missing_mac": "Client MAC address was not found. Open the portal from the UniFi guest network.",
        "client_not_found": "The client was not found in UniFi.",
        "resolve_failed": "Could not identify the Wi-Fi client by IP",
        "sms_failed": "Could not send SMS", "unifi_failed": "The code is correct, but UniFi did not authorize the client",
    },
    "ru": {
        "wifi_login": "Wi-Fi вход", "phone": "Телефон", "get_code": "Получить SMS-код",
        "sending": "Отправляем…", "sms_code": "SMS-код", "enter_code": "Введите код из SMS",
        "code": "Код", "connect": "Подключиться", "invalid_code": "Неверный код. Попробуйте ещё раз.",
        "already_sent": "Код уже отправлен. Введите его ниже.", "done": "Готово",
        "access_open": "Доступ открыт", "access_already_open": "Доступ уже открыт",
        "wifi_authorized": "Wi-Fi авторизован.", "authorized_at": "Время авторизации",
        "valid_until": "Действует до", "error": "Ошибка", "blocked": "Этот клиент заблокирован.",
        "missing_mac": "Не найден MAC-адрес клиента. Откройте портал через гостевую сеть UniFi.",
        "client_not_found": "Клиент не найден в UniFi.",
        "resolve_failed": "Не удалось определить клиента Wi-Fi по IP",
        "sms_failed": "Не удалось отправить SMS", "unifi_failed": "Код верный, но UniFi не авторизовал клиента",
    },
}


@router.get("/assets/ahs.png")
def logo() -> FileResponse:
    return FileResponse(LOGO_PATH)


@router.get("/assets/hotspot-logo")
def custom_logo(settings: Settings = Depends(get_settings)) -> FileResponse:
    logo_path = Path(settings.hotspot_logo_path)
    return FileResponse(logo_path if logo_path.exists() else LOGO_PATH)


@router.post(
    "/api/hotspot/request-code",
    response_model=OtpRequestResponse,
    dependencies=[Depends(require_api_token)],
)
def request_hotspot_code(
    payload: HotspotOtpRequest,
    store: Store = Depends(get_store),
    service: OtpService = Depends(get_otp_service),
) -> OtpRequestResponse:
    hotspot_store = HotspotStore(store)
    existing = hotspot_store.get_session(payload.client_mac)
    if existing and existing["blocked_at"]:
        raise HTTPException(status_code=403, detail="client is blocked")
    hotspot_store.save_session(
        payload.client_mac,
        payload.phone,
        payload.ap_mac,
        payload.redirect_url,
    )
    try:
        service.request_code(payload.phone, _hotspot_purpose(payload.client_mac))
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return OtpRequestResponse(
        ok=True,
        ttl_seconds=service.settings.otp_ttl_seconds,
        resend_after_seconds=service.settings.otp_resend_seconds,
    )


@router.post(
    "/api/hotspot/verify-code",
    response_model=OtpVerifyResponse,
    dependencies=[Depends(require_api_token)],
)
async def verify_hotspot_code(
    payload: HotspotOtpVerifyRequest,
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    service: OtpService = Depends(get_otp_service),
) -> OtpVerifyResponse:
    verified = service.verify_code(payload.phone, _hotspot_purpose(payload.client_mac), payload.code)
    if verified:
        hotspot_store = HotspotStore(store)
        session = hotspot_store.get_session(payload.client_mac)
        ap_mac = session["ap_mac"] if session else None
        await UniFiClient(settings).authorize_guest(payload.client_mac, ap_mac=ap_mac)
        authorized_at = hotspot_store.mark_authorized(payload.client_mac, settings.unifi_auth_minutes)
        session = hotspot_store.get_session(payload.client_mac)
        record_access_event(
            settings,
            build_access_event(
                settings,
                payload.client_mac,
                payload.phone,
                authorized_at,
                session["valid_until"] if session else None,
            ),
        )
    return OtpVerifyResponse(ok=True, verified=verified)


@router.get("/", response_class=HTMLResponse)
@router.get("/portal", response_class=HTMLResponse)
@router.get("/portal/", response_class=HTMLResponse)
@router.get("/guest/s/{site}", response_class=HTMLResponse)
@router.get("/guest/s/{site}/", response_class=HTMLResponse)
@router.get("/guest/s/{site}/login", response_class=HTMLResponse)
@router.get("/guest/s/{site}/login/", response_class=HTMLResponse)
def portal(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
) -> HTMLResponse:
    lang = _portal_lang(request)
    client_mac = (
        request.query_params.get("id")
        or request.query_params.get("mac")
        or request.query_params.get("client_mac")
        or request.query_params.get("clientmac")
        or request.query_params.get("sta")
        or request.query_params.get("client")
        or ""
    )
    ap_mac = request.query_params.get("ap") or request.query_params.get("ap_mac") or ""
    redirect_url = (
        request.query_params.get("url")
        or request.query_params.get("redirect_url")
        or request.query_params.get("redirect")
        or ""
    )
    client_ip = _client_ip(request)
    if not client_mac:
        try:
            client_mac = _resolve_client_mac_from_ip(client_ip, settings)
        except Exception:
            client_mac = ""
    hotspot_store = HotspotStore(store)
    session = hotspot_store.get_session(client_mac) if client_mac else None
    unifi_authorized = _unifi_authorization_state(client_mac, settings) if session and session["authorized_at"] else None
    if _is_authorized(session, settings) and unifi_authorized is not False:
        return HTMLResponse(_page_success(
            settings,
            lang=lang,
            already_authorized=True,
            authorized_at_ts=session["authorized_at"],
            valid_until_ts=session["valid_until"],
        ))
    if session and session["authorized_at"] and unifi_authorized is False:
        hotspot_store.clear_authorized(client_mac)
    return HTMLResponse(_page_request_phone(client_mac, ap_mac, redirect_url, client_ip, lang))


@router.post("/portal/request-code", response_class=HTMLResponse)
def portal_request_code(
    request: Request,
    phone: str = Form(...),
    client_mac: str = Form(default=""),
    client_ip: str = Form(default=""),
    ap_mac: str = Form(default=""),
    redirect_url: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    service: OtpService = Depends(get_otp_service),
) -> HTMLResponse:
    lang = _portal_lang(request)
    if not client_mac:
        try:
            client_mac = _resolve_client_mac_from_ip(client_ip, settings)
        except Exception as exc:
            return HTMLResponse(
                _page_error(f"{_pt(lang, 'resolve_failed')}: {escape(str(exc))}", lang),
                status_code=502,
            )
        if not client_mac:
            return HTMLResponse(_page_missing_client(client_ip, lang), status_code=400)

    hotspot_store = HotspotStore(store)
    existing = hotspot_store.get_session(client_mac)
    if existing and existing["blocked_at"]:
        return HTMLResponse(_page_error(_pt(lang, "blocked"), lang), status_code=403)
    session = hotspot_store.get_session(client_mac)
    unifi_authorized = _unifi_authorization_state(client_mac, settings) if session and session["authorized_at"] else None
    if _is_authorized(session, settings) and unifi_authorized is not False:
        return HTMLResponse(_page_success(
            settings,
            lang=lang,
            already_authorized=True,
            authorized_at_ts=session["authorized_at"],
            valid_until_ts=session["valid_until"],
        ))
    if session and session["authorized_at"] and unifi_authorized is False:
        hotspot_store.clear_authorized(client_mac)

    payload = HotspotOtpRequest(
        phone=phone,
        client_mac=client_mac,
        ap_mac=ap_mac or None,
        redirect_url=redirect_url or None,
    )
    existing = hotspot_store.get_session(payload.client_mac)
    if existing and existing["blocked_at"]:
        return HTMLResponse(_page_error(_pt(lang, "blocked"), lang), status_code=403)
    hotspot_store.save_session(
        payload.client_mac,
        payload.phone,
        payload.ap_mac,
        payload.redirect_url,
    )
    try:
        service.request_code(payload.phone, _hotspot_purpose(payload.client_mac))
    except ValueError:
        return HTMLResponse(
            _page_verify_code(payload.phone, payload.client_mac, redirect_url, lang, already_sent=True)
        )
    except Exception as exc:
        return HTMLResponse(_page_error(f"{_pt(lang, 'sms_failed')}: {escape(str(exc))}", lang), status_code=502)
    return HTMLResponse(_page_verify_code(payload.phone, payload.client_mac, redirect_url, lang))


@router.post("/portal/verify-code")
async def portal_verify_code(
    request: Request,
    phone: str = Form(...),
    client_mac: str = Form(...),
    code: str = Form(...),
    redirect_url: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    store: Store = Depends(get_store),
    service: OtpService = Depends(get_otp_service),
):
    lang = _portal_lang(request)
    payload = HotspotOtpVerifyRequest(phone=phone, client_mac=client_mac, code=code)
    verified = service.verify_code(payload.phone, _hotspot_purpose(payload.client_mac), payload.code)
    hotspot_store = HotspotStore(store)
    session = hotspot_store.get_session(payload.client_mac)
    unifi_authorized = (
        _unifi_authorization_state(payload.client_mac, settings)
        if session and session["authorized_at"]
        else None
    )
    if not verified and _is_authorized(session, settings) and unifi_authorized is not False:
        return HTMLResponse(_page_success(
            settings,
            lang=lang,
            already_authorized=True,
            authorized_at_ts=session["authorized_at"],
            valid_until_ts=session["valid_until"],
        ))
    if not verified and session and session["authorized_at"] and unifi_authorized is False:
        hotspot_store.clear_authorized(payload.client_mac)
    if not verified:
        return HTMLResponse(
            _page_verify_code(payload.phone, payload.client_mac, redirect_url, lang, error=True),
            status_code=400,
        )

    try:
        session = hotspot_store.get_session(payload.client_mac)
        ap_mac = session["ap_mac"] if session else None
        await UniFiClient(settings).authorize_guest(payload.client_mac, ap_mac=ap_mac)
    except Exception as exc:
        return HTMLResponse(
            _page_error(f"{_pt(lang, 'unifi_failed')}: {escape(str(exc))}", lang),
            status_code=502,
        )

    authorized_at = hotspot_store.mark_authorized(payload.client_mac, settings.unifi_auth_minutes)
    session = hotspot_store.get_session(payload.client_mac)
    record_access_event(
        settings,
        build_access_event(
            settings,
            payload.client_mac,
            payload.phone,
            authorized_at,
            session["valid_until"] if session else None,
        ),
    )
    return HTMLResponse(
        _page_success(
            settings,
            lang=lang,
            authorized_at_ts=authorized_at,
            valid_until_ts=session["valid_until"] if session else None,
        )
    )


def _hotspot_purpose(client_mac: str) -> str:
    return f"hotspot:{client_mac.lower()}"


def _portal_lang(request: Request) -> str:
    value = request.query_params.get("lang", "").lower()
    if value in PORTAL_TEXT:
        return value
    accepted = request.headers.get("accept-language", "").lower()
    return "ru" if accepted.startswith("ru") or ",ru" in accepted else "en"


def _pt(lang: str, key: str) -> str:
    return PORTAL_TEXT.get(lang, PORTAL_TEXT["en"]).get(key, key)


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else ""


def _resolve_client_mac_from_ip(client_ip: str, settings: Settings) -> str:
    if not client_ip:
        return ""
    import anyio

    return anyio.run(UniFiClient(settings).find_client_mac_by_ip, client_ip) or ""


def _is_authorized(session, settings: Settings) -> bool:
    if not session or not session["authorized_at"]:
        return False
    valid_until = session["valid_until"] if "valid_until" in session.keys() else None
    if valid_until:
        return int(time.time()) < int(valid_until)
    return int(time.time()) < int(session["authorized_at"]) + settings.unifi_auth_minutes * 60


def _unifi_authorization_state(client_mac: str, settings: Settings) -> Optional[bool]:
    if not client_mac:
        return None
    import anyio

    try:
        return anyio.run(UniFiClient(settings).is_guest_authorized, client_mac)
    except Exception:
        return None


def _authorization_window(
    settings: Settings,
    authorized_at_ts: Optional[int] = None,
    valid_until_ts: Optional[int] = None,
) -> tuple[str, str]:
    authorized_at = (
        datetime.fromtimestamp(int(authorized_at_ts))
        if authorized_at_ts
        else datetime.now()
    )
    expires_at = (
        datetime.fromtimestamp(int(valid_until_ts))
        if valid_until_ts
        else authorized_at + timedelta(minutes=settings.unifi_auth_minutes)
    )
    return (
        authorized_at.strftime("%d.%m.%Y %H:%M"),
        expires_at.strftime("%d.%m.%Y %H:%M"),
    )


def _page_request_phone(
    client_mac: str, ap_mac: str, redirect_url: str, client_ip: str, lang: str
) -> str:
    return f"""
    <!doctype html>
    <html lang="{escape(lang)}">
        <head>{_head(_pt(lang, "wifi_login"))}</head>
      <body>
        <main>
          {_brand()}
          <h1>{escape(get_settings().hotspot_portal_title)}</h1>
          <form id="request-code-form" method="post" action="{_relative_action('request-code')}">
            <input type="hidden" name="lang" value="{escape(lang)}">
            <input type="hidden" name="client_mac" value="{escape(client_mac)}">
            <input type="hidden" name="client_ip" value="{escape(client_ip)}">
            <input type="hidden" name="ap_mac" value="{escape(ap_mac)}">
            <input type="hidden" name="redirect_url" value="{escape(redirect_url)}">
            <label>{_pt(lang, "phone")}<input id="phone" name="phone" type="tel" inputmode="tel" autocomplete="tel-national" placeholder="9991234567" required autofocus></label>
            <button type="submit">{_pt(lang, "get_code")}</button>
          </form>
          <section id="code-step" hidden>
            <h1>{_pt(lang, "enter_code")}</h1>
            <form method="post" action="{_relative_action('verify-code')}">
              <input type="hidden" name="lang" value="{escape(lang)}">
              <input id="verify-phone" type="hidden" name="phone">
              <input type="hidden" name="client_mac" value="{escape(client_mac)}">
              <input type="hidden" name="redirect_url" value="{escape(redirect_url)}">
              <label>{_pt(lang, "code")}<input id="code" name="code" type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" enterkeyhint="done" minlength="4" maxlength="10" autocapitalize="off" spellcheck="false" required></label>
              <button type="submit">{_pt(lang, "connect")}</button>
            </form>
          </section>
          <script>
            const requestForm = document.getElementById("request-code-form");
            requestForm.addEventListener("submit", async (event) => {{
              event.preventDefault();
              if (!requestForm.reportValidity()) return;
              const phone = document.getElementById("phone").value;
              document.getElementById("verify-phone").value = phone;
              requestForm.hidden = true;
              const codeStep = document.getElementById("code-step");
              codeStep.hidden = false;
              const codeInput = document.getElementById("code");
              codeInput.focus();
              try {{
                const response = await fetch(requestForm.action, {{
                  method: "POST",
                  body: new FormData(requestForm),
                }});
                const result = await response.text();
                if (!response.ok || !result.includes('id="code"')) {{
                  document.open();
                  document.write(result);
                  document.close();
                }}
              }} catch (error) {{
                requestForm.submit();
              }}
            }});
          </script>
        </main>
      </body>
    </html>
    """


def _page_missing_client(client_ip: str = "", lang: str = "en") -> str:
    details = (
        f"<p class='error'>IP: {escape(client_ip)}. {_pt(lang, 'client_not_found')}</p>"
        if client_ip
        else ""
    )
    return f"""
    <!doctype html>
    <html lang="{escape(lang)}">
      <head>{_head("Wi-Fi вход")}</head>
      <body>
        <main>
          {_brand()}
          <h1>{escape(get_settings().hotspot_portal_title)}</h1>
          <p class="error">{_pt(lang, "missing_mac")}</p>
          {details}
        </main>
      </body>
    </html>
    """


def _page_verify_code(
    phone: str,
    client_mac: str,
    redirect_url: str,
    lang: str = "en",
    error: bool = False,
    already_sent: bool = False,
) -> str:
    message = f"<p class='error'>{_pt(lang, 'invalid_code')}</p>" if error else ""
    if already_sent:
        message = f"<p>{_pt(lang, 'already_sent')}</p>"
    return f"""
    <!doctype html>
    <html lang="{escape(lang)}">
      <head>{_head(_pt(lang, "sms_code"))}</head>
      <body>
        <main>
          {_brand()}
          <h1>{_pt(lang, "enter_code")}</h1>
          {message}
          <form method="post" action="{_relative_action('verify-code')}">
            <input type="hidden" name="lang" value="{escape(lang)}">
            <input type="hidden" name="phone" value="{escape(phone)}">
            <input type="hidden" name="client_mac" value="{escape(client_mac)}">
            <input type="hidden" name="redirect_url" value="{escape(redirect_url)}">
            <label>{_pt(lang, "code")}<input id="code" name="code" type="text" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" enterkeyhint="done" minlength="4" maxlength="10" autocapitalize="off" spellcheck="false" required autofocus></label>
            <button type="submit">{_pt(lang, "connect")}</button>
          </form>
        </main>
      </body>
    </html>
    """


def _relative_action(action: str) -> str:
    return f"/portal/{action}"


def _page_success(
    settings: Settings,
    lang: str = "en",
    already_authorized: bool = False,
    authorized_at_ts: Optional[int] = None,
    valid_until_ts: Optional[int] = None,
) -> str:
    authorized_at, expires_at = _authorization_window(settings, authorized_at_ts, valid_until_ts)
    title = _pt(lang, "access_already_open" if already_authorized else "access_open")
    return f"""
    <!doctype html>
    <html lang="{escape(lang)}">
      <head>{_head(_pt(lang, "done"))}</head>
      <body>
        <main>
          {_brand()}
          <h1>{title}</h1>
          <p class="success">{_pt(lang, "wifi_authorized")}</p>
          <dl>
            <div><dt>{_pt(lang, "authorized_at")}</dt><dd>{authorized_at}</dd></div>
            <div><dt>{_pt(lang, "valid_until")}</dt><dd>{expires_at}</dd></div>
          </dl>
        </main>
      </body>
    </html>
    """


def _page_error(message: str, lang: str = "en") -> str:
    return f"""
    <!doctype html>
    <html lang="{escape(lang)}">
      <head>{_head(_pt(lang, "error"))}</head>
      <body><main>{_brand()}<h1>{_pt(lang, "error")}</h1><p class="error">{message}</p></main></body>
    </html>
    """


def _brand() -> str:
    logo_path = Path(get_settings().hotspot_logo_path)
    src = "/assets/hotspot-logo" if logo_path.exists() else "/assets/ahs.png"
    return f"""
          <img class="logo" src="{escape(src)}" alt="AHS">
    """


def _head(title: str) -> str:
    return f"""
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{escape(title)}</title>
      <style>
        :root {{ color-scheme: dark; }}
        * {{ box-sizing: border-box; }}
        body {{ min-height: 100vh; margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: radial-gradient(circle at top, #273142 0, #10141b 42%, #07090d 100%); color: #f4f7fb; }}
        body::before {{ content: ""; position: fixed; inset: 0; pointer-events: none; background: linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,0) 24%); }}
        main {{ width: min(420px, calc(100vw - 32px)); margin: 8vh auto 0; padding-bottom: 32px; }}
        .logo {{ display: block; width: 132px; height: 132px; object-fit: contain; margin: 0 auto 28px; filter: drop-shadow(0 18px 28px rgba(0,0,0,.38)); }}
        h1 {{ font-size: 30px; line-height: 1.12; margin: 0 0 26px; text-align: center; letter-spacing: 0; }}
        form {{ display: grid; gap: 16px; }}
        label {{ display: grid; gap: 8px; font-size: 14px; color: #bac4d3; }}
        input {{ width: 100%; border: 1px solid #3a4658; border-radius: 8px; padding: 15px 13px; font-size: 18px; background: #151b24; color: #fff; outline: none; }}
        input:focus {{ border-color: #84b7ff; box-shadow: 0 0 0 3px rgba(132,183,255,.18); }}
        button {{ border: 0; border-radius: 8px; padding: 15px 16px; font-size: 17px; font-weight: 700; background: #f2f5f9; color: #080b10; }}
        p {{ color: #cbd4df; text-align: center; }}
        dl {{ display: grid; gap: 10px; margin: 22px 0 0; }}
        dl div {{ display: flex; justify-content: space-between; gap: 16px; padding: 13px 0; border-bottom: 1px solid #2b3543; }}
        dt {{ color: #9ca8b8; }}
        dd {{ margin: 0; color: #fff; font-weight: 700; text-align: right; }}
        .success {{ color: #b6f2cb; }}
        .error {{ color: #ffb4ab; }}
      </style>
      <script>
        document.addEventListener("DOMContentLoaded", () => {{
          document.querySelectorAll("form").forEach((form) => {{
            form.addEventListener("submit", () => {{
              const button = form.querySelector("button[type=submit]");
              if (!button || button.disabled) return;
              if (button.dataset.sending) button.textContent = button.dataset.sending;
              button.disabled = true;
            }});
          }});
        }});
      </script>
    """
