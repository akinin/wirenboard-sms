import csv
import html
import io
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

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
        "theme": "Toggle theme",
        "export_csv": "Export CSV",
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
        "theme": "Сменить тему",
        "export_csv": "Выгрузить CSV",
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
            _messages(message, error)
            + _settings_form(settings, lang, "archive")
            + _test_sms_form(lang)
            + _archive_table(rows, lang),
            active_tab="archive",
            lang=lang,
        )
    )


@router.get("/archive.csv")
def export_archive_csv(
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
) -> StreamingResponse:
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output)
    writer.writerow(
        ["Name", "MAC", "Phone", "Authorized at", "Valid until", "Duration days", "Status"]
    )
    now = int(time.time())
    for row in HotspotStore(store).list_archive(limit=100000):
        writer.writerow(
            [
                row["display_name"] or "",
                row["client_mac"],
                row["phone"],
                _dt(row["authorized_at"]),
                _dt(row["valid_until"]),
                row["minutes"] // 1440,
                _archive_status(row, now),
            ]
        )
    headers = {"Content-Disposition": 'attachment; filename="hotspot-archive.csv"'}
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv; charset=utf-8", headers=headers)


@router.post("/clients/{client_mac}/extend")
async def extend_client(
    client_mac: str,
    days: int = Form(...),
    lang: str = Form(default="en"),
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    if days not in (1, 2, 7, 365):
        return _redirect(error="Invalid duration", lang=lang)
    hotspot_store = HotspotStore(store)
    session = hotspot_store.get_session(client_mac)
    if not session:
        return _redirect(error="Client was not found", lang=lang)
    now = int(time.time())
    remaining_minutes = max(0, int(session["valid_until"] or now) - now) // 60
    minutes = remaining_minutes + days * 24 * 60
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
    return f"""
    <section>
      <div class="section-head"><h2>{_t(lang, "portal")}</h2></div>
      <form class="settings" method="post" action="/admin/settings" enctype="multipart/form-data">
        <input type="hidden" name="lang" value="{html.escape(lang)}">
        <label>{_t(lang, "welcome_text")}<input name="title" value="{title}" maxlength="120"></label>
        <label class="logo-picker" title="{_t(lang, 'choose_file')}">
          <span>{_t(lang, "logo")}</span>
          <span class="logo-preview">
            <input id="logo-file" name="logo" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml">
            <img id="logo-preview" src="/assets/hotspot-logo" alt="{_t(lang, 'logo')}">
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
            f"<td data-label='{_t(lang, 'client')}'>{_client_identity(session, client, mac, lang)}</td>"
            f"<td data-label='{_t(lang, 'phone')}'>{html.escape(str(session['phone']))}</td>"
            f"<td data-label='{_t(lang, 'ip')}'>{html.escape(str(client.get('ip') or ''))}</td>"
            f"<td data-label='{_t(lang, 'authorized')}'>{_dt(session['authorized_at'])}</td>"
            f"<td data-label='{_t(lang, 'valid_until')}'>{_dt(session['valid_until'])}</td>"
            f"<td data-label='{_t(lang, 'extend')}'>{_extend_actions(mac, lang)}</td>"
            f"<td data-label='{_t(lang, 'revoke')}'>{_revoke_actions(mac, lang)}</td>"
            f"<td data-label='{_t(lang, 'block')}'>{_block_action(mac, lang)}</td>"
            "</tr>"
        )
    body = "\n".join(rows) if rows else f"<tr><td colspan='8' class='empty'>{_t(lang, 'no_active')}</td></tr>"
    return f"""
    <section>
      <div class="section-head"><h2>{_t(lang, "active_clients")}</h2>{_tabs(lang, "active")}</div>
      <table class="active-table">
        <colgroup>
          <col class="col-client"><col class="col-phone"><col class="col-ip">
          <col class="col-date"><col class="col-date"><col class="col-extend">
          <col class="col-action"><col class="col-action">
        </colgroup>
        <thead>
          <tr>
            <th>{_t(lang, "client")}</th><th>{_t(lang, "phone")}</th><th>{_t(lang, "ip")}</th><th>{_t(lang, "authorized")}</th>
            <th>{_t(lang, "valid_until")}</th><th>{_t(lang, "extend")}</th><th>{_t(lang, "revoke")}</th><th>{_t(lang, "block")}</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _client_identity(session, client: dict[str, Any], mac: str, lang: str) -> str:
    name = str(session["display_name"] or client.get("name") or client.get("hostname") or "")
    return f"""
    <button type="button" class="client-display" title="{_t(lang, 'edit')}">{html.escape(name or mac)}</button>
    <small>{html.escape(mac)}</small>
    <form class="client-name" method="post" action="/admin/clients/{html.escape(mac)}/name" hidden>
      <input type="hidden" name="lang" value="{html.escape(lang)}">
      <input name="display_name" value="{html.escape(name)}" maxlength="120" placeholder="{_t(lang, 'name')}">
    </form>
    """


def _extend_actions(mac: str, lang: str = "en") -> str:
    options = "".join(
        f"<button name='days' value='{days}'>+{days}d</button>"
        for days in (1, 2, 7, 365)
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
    </div>
    """


def _block_action(mac: str, lang: str = "en") -> str:
    return f"""
    <div class="actions">
      <form method="post" action="/admin/clients/{html.escape(mac)}/block"><input type="hidden" name="lang" value="{html.escape(lang)}"><button class="danger">{_t(lang, "block")}</button></form>
    </div>
    """


def _archive_table(rows, lang: str) -> str:
    now = int(time.time())
    table_rows = []
    for row in rows:
        status = _archive_status(row, now)
        table_rows.append(
            "<tr>"
            f"<td><strong>{html.escape(str(row['display_name'] or row['client_mac']))}</strong><small>{html.escape(str(row['client_mac']))}</small></td>"
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
      <div class="section-head"><h2>{_t(lang, "archive")}</h2><div class="table-tools"><a class="export-button" href="/admin/archive.csv">{_t(lang, "export_csv")}</a>{_tabs(lang, "archive")}</div></div>
      <table>
        <thead>
          <tr><th>{_t(lang, "client")}</th><th>{_t(lang, "phone")}</th><th>{_t(lang, "authorized")}</th><th>{_t(lang, "valid_until")}</th><th>{_t(lang, "duration")}</th><th>{_t(lang, "status")}</th></tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _archive_status(row, now: int) -> str:
    if row["blocked_at"]:
        return "blocked"
    if row["revoked_at"]:
        return "revoked"
    return "active" if row["valid_until"] > now else "expired"


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
        <link rel="icon" href="/assets/hotspot-logo">
        <title>{html.escape(title)} - SMS Gateway Admin</title>
        <script>
          const savedTheme = localStorage.getItem("sms-theme");
          const initialTheme = savedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
          document.documentElement.dataset.theme = initialTheme;
        </script>
        <style>
          * {{ box-sizing: border-box; }}
          body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background: #f4f5f6; color: #18212b; }}
          header {{ position: relative; display: flex; justify-content: flex-start; align-items: center; padding: 15px 112px 15px 28px; background: #fff; color: #18212b; border-bottom: 1px solid #e5e7ea; box-shadow: 0 1px 2px rgba(0,0,0,.03); }}
          header h1 {{ margin: 0; font-size: 18px; font-weight: 600; letter-spacing: -.01em; }}
          .header-controls {{ position: absolute; right: 28px; top: 50%; transform: translateY(-50%); display: flex; align-items: center; gap: 12px; }}
          .language {{ display: flex; gap: 6px; }}
          .language a, .tabs a {{ color: #64748b; text-decoration: none; font-weight: 800; }}
          .language a {{ color: #88919b; font-size: 12px; }}
          .language a.active {{ color: #006fff; }}
          main {{ max-width: 1440px; margin: 0 auto; padding: 28px; }}
          section {{ margin-bottom: 24px; }}
          .section-head {{ display: flex; align-items: center; justify-content: space-between; gap: 18px; margin: 0 0 14px; }}
          h2 {{ font-size: 18px; font-weight: 600; margin: 0; letter-spacing: -.01em; }}
          section > h2 {{ margin-bottom: 14px; }}
          .tabs {{ display: inline-flex; width: max-content; justify-content: flex-end; gap: 4px; padding: 3px; background: #e9ebed; border-radius: 8px; }}
          .tabs a {{ padding: 7px 12px; border-radius: 6px; font-size: 13px; }}
          .tabs a.active {{ color: #18212b; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
          table {{ width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; background: #fff; border: 1px solid #e1e4e8; border-radius: 10px; box-shadow: 0 1px 2px rgba(0,0,0,.03); }}
          th, td {{ padding: 12px 14px; border-bottom: 1px solid #edf0f2; text-align: left; vertical-align: top; font-size: 13px; }}
          th {{ background: #f8f9fa; color: #69727d; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }}
          .active-table {{ table-layout: fixed; }}
          .active-table .col-client {{ width: 17%; }}
          .active-table .col-phone {{ width: 11%; }}
          .active-table .col-ip {{ width: 8%; }}
          .active-table .col-date {{ width: 12%; }}
          .active-table .col-extend {{ width: 20%; }}
          .active-table .col-action {{ width: 10%; }}
          .active-table td:nth-child(4), .active-table td:nth-child(5) {{ white-space: nowrap; }}
          small {{ display: block; color: #64748b; margin-top: 3px; }}
          form.settings, form.test-sms {{ display: grid; gap: 14px; align-items: end; background: #fff; border: 1px solid #e1e4e8; border-radius: 10px; padding: 18px; box-shadow: 0 1px 2px rgba(0,0,0,.03); }}
          form.settings {{ grid-template-columns: minmax(320px, 1fr) 90px 110px; align-items: end; }}
          form.test-sms {{ grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto; }}
          label {{ display: grid; gap: 6px; font-size: 13px; color: #475569; font-weight: 700; }}
          input {{ border: 1px solid #cfd4da; border-radius: 6px; padding: 9px 10px; background: #fff; font: inherit; outline: none; }}
          input:focus {{ border-color: #006fff; box-shadow: 0 0 0 2px rgba(0,111,255,.14); }}
          input[type=file] {{ position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }}
          .logo-picker {{ align-content: start; cursor: pointer; }}
          .logo-preview {{ width: 64px; height: 64px; display: grid; place-items: center; overflow: hidden; border: 1px solid #dfe3e8; border-radius: 10px; background: #f6f7f8; transition: border-color .15s, box-shadow .15s; }}
          .logo-preview:hover {{ border-color: #006fff; box-shadow: 0 0 0 2px rgba(0,111,255,.12); }}
          .logo-preview img {{ display: block; width: 100%; height: 100%; object-fit: contain; padding: 5px; }}
          button {{ border: 0; border-radius: 6px; padding: 8px 10px; background: #006fff; color: #fff; font-weight: 600; cursor: pointer; }}
          button:hover {{ background: #0062e5; }}
          .save-button {{ min-width: 96px; width: auto; justify-self: start; min-height: 38px; padding: 0 16px; }}
          .settings .save-button {{ width: 100%; justify-self: stretch; }}
          button.danger {{ background: #b91c1c; }}
          .actions {{ display: grid; gap: 8px; min-width: 0; }}
          .actions form {{ display: flex; flex-wrap: wrap; gap: 6px; }}
          .actions button {{ white-space: nowrap; }}
          .client-display {{ padding: 0; background: none; color: #18212b; font-weight: 600; text-align: left; border-bottom: 1px dashed transparent; }}
          .client-display:hover {{ background: none; color: #006fff; border-bottom-color: #006fff; }}
          .client-name {{ margin-top: 5px; min-width: 170px; }}
          .client-name input {{ min-width: 0; width: 170px; padding: 6px 8px; }}
          .notice {{ padding: 11px 13px; border-radius: 6px; font-weight: 700; }}
          .success {{ background: #dcfce7; color: #166534; }}
          .error {{ background: #fee2e2; color: #991b1b; }}
          .empty {{ color: #64748b; text-align: center; padding: 24px; }}
          .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #e2e8f0; font-weight: 700; }}
          .badge.active {{ background: #dcfce7; color: #166534; }}
          .badge.revoked {{ background: #fef3c7; color: #92400e; }}
          .badge.blocked {{ background: #fee2e2; color: #991b1b; }}
          .table-tools {{ display: flex; align-items: center; gap: 10px; }}
          .export-button {{ display: inline-flex; align-items: center; min-height: 34px; padding: 0 12px; border: 1px solid #cfd4da; border-radius: 7px; background: #fff; color: #39424e; font-size: 13px; font-weight: 600; text-decoration: none; }}
          .export-button:hover {{ border-color: #006fff; color: #006fff; }}
          .theme-toggle {{ width: 34px; height: 34px; display: grid; place-items: center; padding: 0; border: 1px solid #dfe3e8; border-radius: 8px; background: #f7f8f9; color: #5f6873; }}
          .theme-toggle:hover {{ background: #eef5ff; color: #006fff; }}
          .theme-toggle svg {{ width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }}
          .moon-icon {{ display: none; }}
          [data-theme="dark"] {{ color-scheme: dark; }}
          [data-theme="dark"] body {{ background: #11151a; color: #e8eaed; }}
          [data-theme="dark"] header {{ background: #181d23; color: #f2f4f6; border-color: #2c333b; box-shadow: none; }}
          [data-theme="dark"] h2, [data-theme="dark"] .client-display {{ color: #e8eaed; }}
          [data-theme="dark"] form.settings, [data-theme="dark"] form.test-sms, [data-theme="dark"] table {{ background: #181d23; border-color: #303741; box-shadow: none; }}
          [data-theme="dark"] th {{ background: #20262d; color: #9aa3ad; }}
          [data-theme="dark"] th, [data-theme="dark"] td {{ border-bottom-color: #2b323a; }}
          [data-theme="dark"] label, [data-theme="dark"] small {{ color: #a6afb9; }}
          [data-theme="dark"] input {{ background: #11161c; border-color: #3a424c; color: #edf0f2; }}
          [data-theme="dark"] .logo-preview {{ background: #11161c; border-color: #3a424c; }}
          [data-theme="dark"] .tabs {{ background: #262c33; }}
          [data-theme="dark"] .tabs a.active {{ background: #3a424c; color: #fff; box-shadow: none; }}
          [data-theme="dark"] .theme-toggle {{ background: #242a31; border-color: #3a424c; color: #d6dbe0; }}
          [data-theme="dark"] .export-button {{ background: #181d23; border-color: #3a424c; color: #d6dbe0; }}
          [data-theme="dark"] .sun-icon {{ display: none; }}
          [data-theme="dark"] .moon-icon {{ display: block; }}
          @media (max-width: 900px) {{
            header {{ justify-content: flex-start; padding-right: 96px; }}
            .section-head {{ display: flex; flex-wrap: wrap; align-items: center; }}
            .tabs {{ flex: 0 0 auto; justify-content: flex-start; margin: 0; }}
            .table-tools {{ flex: 0 0 auto; align-items: center; margin: 0; }}
            main {{ padding: 14px; overflow-x: hidden; }}
            form.settings {{ grid-template-columns: minmax(0, 1fr) 76px; align-items: end; }}
            form.settings > label:first-of-type {{ grid-column: 1; grid-row: 1; }}
            form.settings .logo-picker {{ grid-column: 2; grid-row: 1; }}
            form.settings .logo-preview {{ width: 64px; height: 64px; }}
            form.settings .save-button {{ grid-column: 1; grid-row: 2; width: auto; justify-self: start; }}
            form.test-sms {{ grid-template-columns: 1fr; }}
            .active-table {{ display: block; table-layout: auto; border: 0; background: transparent; box-shadow: none; overflow: visible; }}
            .active-table colgroup, .active-table thead {{ display: none; }}
            .active-table tbody {{ display: grid; gap: 12px; }}
            .active-table tr {{ display: block; overflow: hidden; border: 1px solid #e1e4e8; border-radius: 10px; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.03); }}
            .active-table td {{ display: grid; grid-template-columns: minmax(105px, 38%) 1fr; gap: 10px; align-items: start; width: 100%; padding: 11px 13px; white-space: normal !important; }}
            .active-table td::before {{ content: attr(data-label); color: #7a838d; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }}
            .active-table td:last-child {{ border-bottom: 0; }}
            .active-table .actions {{ width: 100%; }}
            .active-table .actions form {{ justify-content: flex-start; }}
            [data-theme="dark"] .active-table tr {{ background: #181d23; border-color: #303741; box-shadow: none; }}
          }}
          @media (max-width: 520px) {{
            .section-head {{ align-items: flex-start; }}
            .section-head > h2 {{ flex: 1 0 100%; margin-bottom: 10px; }}
            .table-tools {{ flex-wrap: wrap; }}
            .export-button, .tabs a {{ min-height: 32px; padding-left: 10px; padding-right: 10px; }}
          }}
        </style>
      </head>
      <body>
        <header>
          <h1>SMS Gateway Admin</h1>
          <div class="header-controls">
            <div class="language"><a href="?lang=ru" {ru_active}>RU</a><a href="?lang=en" {en_active}>EN</a></div>
            <button id="theme-toggle" class="theme-toggle" type="button" title="{_t(lang, 'theme')}" aria-label="{_t(lang, 'theme')}">
              <svg class="sun-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41"></path></svg>
              <svg class="moon-icon" viewBox="0 0 24 24"><path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z"></path></svg>
            </button>
          </div>
        </header>
        <main>{content}</main>
        <script>
          const logoInput = document.getElementById("logo-file");
          const logoPreview = document.getElementById("logo-preview");
          if (logoInput && logoPreview) {{
            logoInput.addEventListener("change", () => {{
              if (logoInput.files.length) logoPreview.src = URL.createObjectURL(logoInput.files[0]);
            }});
          }}
          document.getElementById("theme-toggle").addEventListener("click", () => {{
            const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
            document.documentElement.dataset.theme = next;
            localStorage.setItem("sms-theme", next);
          }});
          document.querySelectorAll(".client-display").forEach((display) => {{
            display.addEventListener("click", () => {{
              const form = display.parentElement.querySelector(".client-name");
              display.hidden = true;
              form.hidden = false;
              const input = form.querySelector("input[name=display_name]");
              input.dataset.original = input.value;
              input.focus();
              input.select();
              input.addEventListener("blur", () => {{
                if (input.value !== input.dataset.original) form.requestSubmit();
                else {{ form.hidden = true; display.hidden = false; }}
              }}, {{ once: true }});
            }});
          }});
        </script>
      </body>
    </html>
    """
