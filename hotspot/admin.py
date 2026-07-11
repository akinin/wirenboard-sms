import html
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from api.config import Settings, get_settings
from api.deps import get_store
from api.models import normalize_phone
from api.sms import SmsSender
from api.store import Store

from .audit import build_access_event, record_access_event
from .store import HotspotStore
from .unifi import UniFiClient

router = APIRouter(prefix="/admin")

TEXT = {
    "en": {
        "active": "Active",
        "archive": "Archive",
        "portal": "Portal",
        "welcome_text": "Welcome text",
        "logo": "Logo",
        "choose_file": "Choose file",
        "no_file": "No file selected",
        "save": "Save",
        "test_sms": "Test SMS",
        "phone": "Phone",
        "message": "Message",
        "send": "Send",
        "active_clients": "Active clients",
        "client": "Client",
        "ip": "IP",
        "authorized": "Authorized",
        "valid_until": "Valid until",
        "traffic": "Traffic",
        "unifi": "UniFi",
        "actions": "Actions",
        "extend": "Extend",
        "edit": "Edit",
        "name": "Name",
        "no_active": "No active clients",
        "duration": "Duration",
        "status": "Status",
        "empty_archive": "Archive is empty",
        "revoke": "Revoke",
        "block": "Block",
        "portal_saved": "Portal settings updated",
        "sms_sent": "Test SMS sent",
    },
    "ru": {
        "active": "Активные",
        "archive": "Архив",
        "portal": "Портал",
        "welcome_text": "Текст приветствия",
        "logo": "Логотип",
        "choose_file": "Выберите файл",
        "no_file": "Файл не выбран",
        "save": "Сохранить",
        "test_sms": "Тестовая SMS",
        "phone": "Телефон",
        "message": "Текст сообщения",
        "send": "Отправить",
        "active_clients": "Активные клиенты",
        "client": "Клиент",
        "ip": "IP",
        "authorized": "Авторизован",
        "valid_until": "Действует до",
        "traffic": "Трафик",
        "unifi": "UniFi",
        "actions": "Действия",
        "extend": "Продлить",
        "edit": "Изменить",
        "name": "Имя",
        "no_active": "Активных клиентов нет",
        "duration": "Срок",
        "status": "Статус",
        "empty_archive": "Архив пуст",
        "revoke": "Отозвать",
        "block": "Блокировать",
        "portal_saved": "Настройки портала сохранены",
        "sms_sent": "Тестовая SMS отправлена",
    },
}


def require_admin(settings: Settings = Depends(get_settings)) -> Settings:
    if settings.app_role != "admin":
        raise HTTPException(status_code=404, detail="not found")
    return settings


@router.get("/", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
) -> HTMLResponse:
    lang = _lang(request)
    hotspot_store = HotspotStore(store)
    sessions = hotspot_store.list_active_sessions()
    unifi_clients = await _safe_unifi_clients(settings)
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    return HTMLResponse(
        _layout(
            "Active clients",
            _messages(message, error)
            + _settings_form(settings, lang, "active")
            + _test_sms_form(lang)
            + _active_table(settings, sessions, unifi_clients, lang),
            active_tab="active",
            lang=lang,
        )
    )


@router.get("/archive", response_class=HTMLResponse)
def admin_archive(
    request: Request,
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
) -> HTMLResponse:
    lang = _lang(request)
    rows = HotspotStore(store).list_archive()
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    return HTMLResponse(
        _layout(
            "Archive",
            _messages(message, error) + _archive_table(rows, lang),
            active_tab="archive",
            lang=lang,
        )
    )


@router.post("/clients/{client_mac}/extend")
async def extend_client(
    client_mac: str,
    days: int = Form(...),
    lang: str = Form(default="en"),
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    if days not in (1, 2, 7, 14, 30, 365):
        return _redirect(error="Invalid duration", lang=lang)
    hotspot_store = HotspotStore(store)
    session = hotspot_store.get_session(client_mac)
    if not session:
        return _redirect(error="Client was not found", lang=lang)
    minutes = days * 24 * 60
    try:
        await UniFiClient(settings).authorize_guest(
            client_mac,
            minutes=minutes,
            ap_mac=session["ap_mac"],
        )
    except Exception as exc:
        return _redirect(error=f"UniFi authorize failed: {exc}", lang=lang)
    authorized_at = hotspot_store.mark_authorized(client_mac, minutes)
    session = hotspot_store.get_session(client_mac)
    record_access_event(
        settings,
        build_access_event(
            settings,
            client_mac,
            session["phone"],
            authorized_at,
            session["valid_until"],
        ),
    )
    return _redirect(message=f"Authorization extended for {days} day(s)", lang=lang)


@router.post("/clients/{client_mac}/revoke")
async def revoke_client(
    client_mac: str,
    lang: str = Form(default="en"),
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    try:
        await UniFiClient(settings).unauthorize_guest(client_mac)
    except Exception as exc:
        return _redirect(error=f"UniFi revoke failed: {exc}", lang=lang)
    HotspotStore(store).clear_authorized(client_mac)
    return _redirect(message="Authorization revoked", lang=lang)


@router.post("/clients/{client_mac}/name")
def update_client_name(
    client_mac: str,
    display_name: str = Form(default=""),
    lang: str = Form(default="en"),
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    if not HotspotStore(store).set_display_name(client_mac, display_name):
        return _redirect(error="Client was not found", lang=lang)
    return _redirect(message="Client name updated", lang=lang)


@router.post("/clients/{client_mac}/block")
async def block_client(
    client_mac: str,
    lang: str = Form(default="en"),
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    try:
        await UniFiClient(settings).block_client(client_mac)
    except Exception as exc:
        return _redirect(error=f"UniFi block failed: {exc}", lang=lang)
    HotspotStore(store).mark_blocked(client_mac, "Blocked from admin")
    return _redirect(message="Client blocked", lang=lang)


@router.post("/settings")
async def update_settings(
    title: str = Form(...),
    lang: str = Form(default="en"),
    logo: Optional[UploadFile] = File(default=None),
    settings: Settings = Depends(require_admin),
):
    title = title.strip()[:120] or "Welcome"
    _set_env_value(Path(".env"), "HOTSPOT_PORTAL_TITLE", title)
    if logo and logo.filename:
        suffix = Path(logo.filename).suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            return _redirect(error="Unsupported logo format", lang=lang)
        logo_path = Path("./data") / f"hotspot_logo{suffix}"
        logo_path.parent.mkdir(parents=True, exist_ok=True)
        with logo_path.open("wb") as output:
            shutil.copyfileobj(logo.file, output)
        _set_env_value(Path(".env"), "HOTSPOT_LOGO_PATH", str(logo_path))
    get_settings.cache_clear()
    return _redirect(message=_t(lang, "portal_saved"), lang=lang)


@router.post("/test-sms")
def send_test_sms(
    phone: str = Form(...),
    message: str = Form(...),
    lang: str = Form(default="en"),
    settings: Settings = Depends(require_admin),
):
    try:
        message = message.strip()
        if not message:
            raise ValueError("message is empty")
        SmsSender(settings).send(normalize_phone(phone), message)
    except Exception as exc:
        return _redirect(error=f"SMS send failed: {exc}", lang=lang)
    return _redirect(message=_t(lang, "sms_sent"), lang=lang)


async def _safe_unifi_clients(settings: Settings) -> dict[str, dict[str, Any]]:
    try:
        clients = await UniFiClient(settings).list_clients()
    except Exception:
        return {}
    return {
        str(client.get("mac", "")).lower(): client
        for client in clients
        if client.get("mac")
    }


def _set_env_value(path: Path, key: str, value: str) -> None:
    safe_value = value.replace("\r", " ").replace("\n", " ").strip()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={safe_value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={safe_value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _lang(request: Request) -> str:
    value = request.query_params.get("lang", "").lower()
    if value in TEXT:
        return value
    accepted = request.headers.get("accept-language", "").lower()
    return "ru" if accepted.startswith("ru") or ",ru" in accepted else "en"


def _t(lang: str, key: str) -> str:
    return TEXT.get(lang, TEXT["en"]).get(key, key)


def _redirect(message: str = "", error: str = "", lang: str = "en") -> RedirectResponse:
    suffix = f"lang={_url(lang if lang in TEXT else 'en')}"
    if error:
        return RedirectResponse(f"/admin/?{suffix}&error={_url(error)}", status_code=303)
    if message:
        return RedirectResponse(f"/admin/?{suffix}&message={_url(message)}", status_code=303)
    return RedirectResponse(f"/admin/?{suffix}", status_code=303)


def _url(value: str) -> str:
    from urllib.parse import quote

    return quote(value)


def _messages(message: str, error: str) -> str:
    if error:
        return f"<p class='notice error'>{html.escape(error)}</p>"
    if message:
        return f"<p class='notice success'>{html.escape(message)}</p>"
    return ""


def _settings_form(settings: Settings, lang: str, active_tab: str) -> str:
    title = html.escape(settings.hotspot_portal_title)
    tabs = _tabs(lang, active_tab)
    return f"""
    <section>
      <div class="section-head"><h2>{_t(lang, "portal")}</h2>{tabs}</div>
      <form class="settings" method="post" action="/admin/settings" enctype="multipart/form-data">
        <input type="hidden" name="lang" value="{html.escape(lang)}">
        <label>{_t(lang, "welcome_text")}<input name="title" value="{title}" maxlength="120"></label>
        <label>{_t(lang, "logo")}
          <span class="file-picker">
            <input id="logo-file" name="logo" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml">
            <span class="file-button">{_t(lang, "choose_file")}</span>
            <span class="file-name" id="logo-file-name">{_t(lang, "no_file")}</span>
          </span>
        </label>
        <button class="save-button" type="submit">{_t(lang, "save")}</button>
      </form>
    </section>
    """


def _test_sms_form(lang: str) -> str:
    return f"""
    <section>
      <h2>{_t(lang, "test_sms")}</h2>
      <form class="test-sms" method="post" action="/admin/test-sms">
        <input type="hidden" name="lang" value="{html.escape(lang)}">
        <label>{_t(lang, "phone")}<input name="phone" type="tel" placeholder="+79991234567" required></label>
        <label>{_t(lang, "message")}<input name="message" maxlength="1000" required></label>
        <button class="save-button" type="submit">{_t(lang, "send")}</button>
      </form>
    </section>
    """


def _active_table(
    settings: Settings,
    sessions,
    unifi_clients: dict[str, dict[str, Any]],
    lang: str,
) -> str:
    rows = []
    for session in sessions:
        mac = str(session["client_mac"]).lower()
        client = unifi_clients.get(mac, {})
        rows.append(
            "<tr>"
            f"<td>{_client_identity(session, client, mac, lang)}</td>"
            f"<td>{html.escape(str(session['phone']))}</td>"
            f"<td>{html.escape(str(client.get('ip') or ''))}</td>"
            f"<td>{_dt(session['authorized_at'])}</td>"
            f"<td>{_dt(session['valid_until'])}</td>"
            f"<td>{_extend_actions(mac, lang)}</td>"
            f"<td>{_revoke_actions(mac, lang)}</td>"
            "</tr>"
        )
    body = "\n".join(rows) if rows else f"<tr><td colspan='7' class='empty'>{_t(lang, 'no_active')}</td></tr>"
    return f"""
    <section>
      <h2>{_t(lang, "active_clients")}</h2>
      <table>
        <thead>
          <tr>
            <th>{_t(lang, "client")}</th><th>{_t(lang, "phone")}</th><th>{_t(lang, "ip")}</th><th>{_t(lang, "authorized")}</th>
            <th>{_t(lang, "valid_until")}</th><th>{_t(lang, "extend")}</th><th>{_t(lang, "revoke")}</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _client_identity(session, client: dict[str, Any], mac: str, lang: str) -> str:
    name = str(session["display_name"] or client.get("name") or client.get("hostname") or "")
    return f"""
    <strong>{html.escape(name or mac)}</strong>
    <small>{html.escape(mac)}</small>
    <form class="client-name" method="post" action="/admin/clients/{html.escape(mac)}/name">
      <input type="hidden" name="lang" value="{html.escape(lang)}">
      <input name="display_name" value="{html.escape(name)}" maxlength="120" placeholder="{_t(lang, 'name')}">
      <button>{_t(lang, 'edit')}</button>
    </form>
    """


def _extend_actions(mac: str, lang: str = "en") -> str:
    options = "".join(
        f"<button name='days' value='{days}'>{days}d</button>"
        for days in (1, 2, 7, 14, 30, 365)
    )
    return f"""
    <div class="actions">
      <form method="post" action="/admin/clients/{html.escape(mac)}/extend"><input type="hidden" name="lang" value="{html.escape(lang)}">{options}</form>
    </div>
    """


def _revoke_actions(mac: str, lang: str = "en") -> str:
    return f"""
    <div class="actions">
      <form method="post" action="/admin/clients/{html.escape(mac)}/revoke"><input type="hidden" name="lang" value="{html.escape(lang)}"><button>{_t(lang, "revoke")}</button></form>
      <form method="post" action="/admin/clients/{html.escape(mac)}/block"><input type="hidden" name="lang" value="{html.escape(lang)}"><button class="danger">{_t(lang, "block")}</button></form>
    </div>
    """


def _archive_table(rows, lang: str) -> str:
    now = int(time.time())
    table_rows = []
    for row in rows:
        status = "active" if row["valid_until"] > now and not row["revoked_at"] and not row["blocked_at"] else "expired"
        if row["revoked_at"]:
            status = "revoked"
        if row["blocked_at"]:
            status = "blocked"
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row['client_mac']))}</td>"
            f"<td>{html.escape(str(row['phone']))}</td>"
            f"<td>{_dt(row['authorized_at'])}</td>"
            f"<td>{_dt(row['valid_until'])}</td>"
            f"<td>{row['minutes'] // 1440}d</td>"
            f"<td><span class='badge {status}'>{status}</span></td>"
            "</tr>"
        )
    body = "\n".join(table_rows) if table_rows else f"<tr><td colspan='6' class='empty'>{_t(lang, 'empty_archive')}</td></tr>"
    return f"""
    <section>
      <div class="section-head"><h2>{_t(lang, "archive")}</h2>{_tabs(lang, "archive")}</div>
      <table>
        <thead>
          <tr><th>MAC</th><th>{_t(lang, "phone")}</th><th>{_t(lang, "authorized")}</th><th>{_t(lang, "valid_until")}</th><th>{_t(lang, "duration")}</th><th>{_t(lang, "status")}</th></tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _client_meta(client: dict[str, Any]) -> str:
    parts = []
    for key in ("essid", "ap_mac", "radio", "channel", "signal"):
        if client.get(key) is not None:
            parts.append(f"{key}: {client[key]}")
    return "<br>".join(html.escape(str(part)) for part in parts)


def _traffic(client: dict[str, Any]) -> str:
    rx = _first_int(client, "rx_bytes", "bytes-r", "wired-rx_bytes", "rx_bytes-r")
    tx = _first_int(client, "tx_bytes", "bytes-t", "wired-tx_bytes", "tx_bytes-r")
    if rx is None and tx is None:
        return ""
    return f"RX {_bytes(rx or 0)}<br>TX {_bytes(tx or 0)}"


def _first_int(client: dict[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = client.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


def _dt(value) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")


def _tabs(lang: str, active_tab: str) -> str:
    active = "class='active'" if active_tab == "active" else ""
    archive = "class='active'" if active_tab == "archive" else ""
    return f"""
    <nav class="tabs">
      <a href="/admin/?lang={html.escape(lang)}" {active}>{_t(lang, "active")}</a>
      <a href="/admin/archive?lang={html.escape(lang)}" {archive}>{_t(lang, "archive")}</a>
    </nav>
    """


def _layout(title: str, content: str, active_tab: str, lang: str) -> str:
    ru_active = "class='active'" if lang == "ru" else ""
    en_active = "class='active'" if lang == "en" else ""
    return f"""
    <!doctype html>
    <html lang="{html.escape(lang)}">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{html.escape(title)} - SMS Gateway Admin</title>
        <style>
          * {{ box-sizing: border-box; }}
          body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f5f7fa; color: #17202c; }}
          header {{ position: relative; display: flex; justify-content: center; align-items: center; padding: 18px 112px 18px 28px; background: #111827; color: #fff; }}
          header h1 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
          .language {{ position: absolute; right: 28px; top: 50%; transform: translateY(-50%); display: flex; gap: 6px; }}
          .language a, .tabs a {{ color: #64748b; text-decoration: none; font-weight: 800; }}
          .language a {{ color: #cbd5e1; font-size: 13px; }}
          .language a.active {{ color: #fff; }}
          main {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
          section {{ margin-bottom: 24px; }}
          .section-head {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; margin: 0 0 14px; }}
          h2 {{ font-size: 18px; margin: 0; }}
          .tabs {{ display: flex; justify-content: flex-end; gap: 18px; }}
          .tabs a.active {{ color: #111827; }}
          table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d9e0ea; }}
          th, td {{ padding: 11px 12px; border-bottom: 1px solid #e6ebf2; text-align: left; vertical-align: top; font-size: 14px; }}
          th {{ background: #edf2f7; color: #475569; font-size: 12px; text-transform: uppercase; }}
          small {{ display: block; color: #64748b; margin-top: 3px; }}
          form.settings, form.test-sms {{ display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto; gap: 12px; align-items: end; background: #fff; border: 1px solid #d9e0ea; padding: 16px; }}
          label {{ display: grid; gap: 6px; font-size: 13px; color: #475569; font-weight: 700; }}
          input {{ border: 1px solid #cbd5e1; border-radius: 6px; padding: 9px 10px; font: inherit; }}
          input[type=file] {{ position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }}
          .file-picker {{ display: flex; align-items: center; gap: 10px; min-height: 38px; }}
          .file-button {{ display: inline-flex; align-items: center; min-height: 38px; padding: 0 12px; border-radius: 6px; background: #1f2937; color: #fff; cursor: pointer; white-space: nowrap; }}
          .file-name {{ color: #64748b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
          button {{ border: 0; border-radius: 6px; padding: 8px 10px; background: #1f2937; color: #fff; font-weight: 700; cursor: pointer; }}
          .save-button {{ min-width: 96px; width: auto; justify-self: start; min-height: 38px; padding: 0 16px; }}
          button.danger {{ background: #b91c1c; }}
          .actions {{ display: grid; gap: 8px; min-width: 260px; }}
          .actions form {{ display: flex; flex-wrap: wrap; gap: 6px; }}
          .client-name {{ display: flex; gap: 6px; margin-top: 8px; min-width: 230px; }}
          .client-name input {{ min-width: 0; width: 150px; padding: 6px 8px; }}
          .client-name button {{ padding: 6px 8px; }}
          .notice {{ padding: 11px 13px; border-radius: 6px; font-weight: 700; }}
          .success {{ background: #dcfce7; color: #166534; }}
          .error {{ background: #fee2e2; color: #991b1b; }}
          .empty {{ color: #64748b; text-align: center; padding: 24px; }}
          .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #e2e8f0; font-weight: 700; }}
          .badge.active {{ background: #dcfce7; color: #166534; }}
          .badge.revoked {{ background: #fef3c7; color: #92400e; }}
          .badge.blocked {{ background: #fee2e2; color: #991b1b; }}
          @media (max-width: 900px) {{
            header {{ justify-content: flex-start; padding-right: 96px; }}
            .section-head {{ display: block; }}
            .tabs {{ justify-content: flex-start; margin-top: 10px; }}
            main {{ padding: 14px; overflow-x: auto; }}
            form.settings, form.test-sms {{ grid-template-columns: 1fr; }}
          }}
        </style>
      </head>
      <body>
        <header>
          <h1>SMS Gateway Admin</h1>
          <div class="language"><a href="?lang=ru" {ru_active}>RU</a><a href="?lang=en" {en_active}>EN</a></div>
        </header>
        <main>{content}</main>
        <script>
          const logoInput = document.getElementById("logo-file");
          const logoName = document.getElementById("logo-file-name");
          if (logoInput && logoName) {{
            logoInput.addEventListener("change", () => {{
              logoName.textContent = logoInput.files.length ? logoInput.files[0].name : "{_t(lang, "no_file")}";
            }});
          }}
        </script>
      </body>
    </html>
    """
