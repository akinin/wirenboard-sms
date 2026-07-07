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
from api.store import Store

from .audit import build_access_event, record_access_event
from .store import HotspotStore
from .unifi import UniFiClient

router = APIRouter(prefix="/admin")


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
    hotspot_store = HotspotStore(store)
    sessions = hotspot_store.list_active_sessions()
    unifi_clients = await _safe_unifi_clients(settings)
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    return HTMLResponse(
        _layout(
            "Active clients",
            _messages(message, error)
            + _settings_form(settings)
            + _active_table(settings, sessions, unifi_clients),
            active_tab="active",
        )
    )


@router.get("/archive", response_class=HTMLResponse)
def admin_archive(
    request: Request,
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
) -> HTMLResponse:
    rows = HotspotStore(store).list_archive()
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    return HTMLResponse(
        _layout(
            "Archive",
            _messages(message, error) + _archive_table(rows),
            active_tab="archive",
        )
    )


@router.post("/clients/{client_mac}/extend")
async def extend_client(
    client_mac: str,
    days: int = Form(...),
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    if days not in (1, 2, 7, 14, 30, 365):
        return _redirect(error="Invalid duration")
    hotspot_store = HotspotStore(store)
    session = hotspot_store.get_session(client_mac)
    if not session:
        return _redirect(error="Client was not found")
    minutes = days * 24 * 60
    try:
        await UniFiClient(settings).authorize_guest(
            client_mac,
            minutes=minutes,
            ap_mac=session["ap_mac"],
        )
    except Exception as exc:
        return _redirect(error=f"UniFi authorize failed: {exc}")
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
    return _redirect(message=f"Authorization extended for {days} day(s)")


@router.post("/clients/{client_mac}/revoke")
async def revoke_client(
    client_mac: str,
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    try:
        await UniFiClient(settings).unauthorize_guest(client_mac)
    except Exception as exc:
        return _redirect(error=f"UniFi revoke failed: {exc}")
    HotspotStore(store).clear_authorized(client_mac)
    return _redirect(message="Authorization revoked")


@router.post("/clients/{client_mac}/block")
async def block_client(
    client_mac: str,
    settings: Settings = Depends(require_admin),
    store: Store = Depends(get_store),
):
    try:
        await UniFiClient(settings).block_client(client_mac)
    except Exception as exc:
        return _redirect(error=f"UniFi block failed: {exc}")
    HotspotStore(store).mark_blocked(client_mac, "Blocked from admin")
    return _redirect(message="Client blocked")


@router.post("/settings")
async def update_settings(
    title: str = Form(...),
    logo: Optional[UploadFile] = File(default=None),
    settings: Settings = Depends(require_admin),
):
    title = title.strip()[:120] or "Welcome"
    _set_env_value(Path(".env"), "HOTSPOT_PORTAL_TITLE", title)
    if logo and logo.filename:
        suffix = Path(logo.filename).suffix.lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
            return _redirect(error="Unsupported logo format")
        logo_path = Path("./data") / f"hotspot_logo{suffix}"
        logo_path.parent.mkdir(parents=True, exist_ok=True)
        with logo_path.open("wb") as output:
            shutil.copyfileobj(logo.file, output)
        _set_env_value(Path(".env"), "HOTSPOT_LOGO_PATH", str(logo_path))
    get_settings.cache_clear()
    return _redirect(message="Portal settings updated")


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


def _redirect(message: str = "", error: str = "") -> RedirectResponse:
    if error:
        return RedirectResponse(f"/admin/?error={_url(error)}", status_code=303)
    if message:
        return RedirectResponse(f"/admin/?message={_url(message)}", status_code=303)
    return RedirectResponse("/admin/", status_code=303)


def _url(value: str) -> str:
    from urllib.parse import quote

    return quote(value)


def _messages(message: str, error: str) -> str:
    if error:
        return f"<p class='notice error'>{html.escape(error)}</p>"
    if message:
        return f"<p class='notice success'>{html.escape(message)}</p>"
    return ""


def _settings_form(settings: Settings) -> str:
    title = html.escape(settings.hotspot_portal_title)
    return f"""
    <section>
      <h2>Portal</h2>
      <form class="settings" method="post" action="/admin/settings" enctype="multipart/form-data">
        <label>Welcome text<input name="title" value="{title}" maxlength="120"></label>
        <label>Logo<input name="logo" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml"></label>
        <button type="submit">Save</button>
      </form>
    </section>
    """


def _active_table(
    settings: Settings,
    sessions,
    unifi_clients: dict[str, dict[str, Any]],
) -> str:
    rows = []
    for session in sessions:
        mac = str(session["client_mac"]).lower()
        client = unifi_clients.get(mac, {})
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(mac)}</strong><small>{html.escape(str(client.get('name') or client.get('hostname') or ''))}</small></td>"
            f"<td>{html.escape(str(session['phone']))}</td>"
            f"<td>{html.escape(str(client.get('ip') or ''))}</td>"
            f"<td>{_dt(session['authorized_at'])}</td>"
            f"<td>{_dt(session['valid_until'])}</td>"
            f"<td>{_traffic(client)}</td>"
            f"<td>{_client_meta(client)}</td>"
            f"<td>{_actions(mac)}</td>"
            "</tr>"
        )
    body = "\n".join(rows) if rows else "<tr><td colspan='8' class='empty'>No active clients</td></tr>"
    return f"""
    <section>
      <h2>Active clients</h2>
      <table>
        <thead>
          <tr>
            <th>Client</th><th>Phone</th><th>IP</th><th>Authorized</th>
            <th>Valid until</th><th>Traffic</th><th>UniFi</th><th>Actions</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
    </section>
    """


def _actions(mac: str) -> str:
    options = "".join(
        f"<button name='days' value='{days}'>{days}d</button>"
        for days in (1, 2, 7, 14, 30, 365)
    )
    return f"""
    <div class="actions">
      <form method="post" action="/admin/clients/{html.escape(mac)}/extend">{options}</form>
      <form method="post" action="/admin/clients/{html.escape(mac)}/revoke"><button>Revoke</button></form>
      <form method="post" action="/admin/clients/{html.escape(mac)}/block"><button class="danger">Block</button></form>
    </div>
    """


def _archive_table(rows) -> str:
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
    body = "\n".join(table_rows) if table_rows else "<tr><td colspan='6' class='empty'>Archive is empty</td></tr>"
    return f"""
    <section>
      <h2>Archive</h2>
      <table>
        <thead>
          <tr><th>MAC</th><th>Phone</th><th>Authorized</th><th>Valid until</th><th>Duration</th><th>Status</th></tr>
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


def _layout(title: str, content: str, active_tab: str) -> str:
    active = "class='active'" if active_tab == "active" else ""
    archive = "class='active'" if active_tab == "archive" else ""
    return f"""
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{html.escape(title)} - SMS Gateway Admin</title>
        <style>
          * {{ box-sizing: border-box; }}
          body {{ margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #f5f7fa; color: #17202c; }}
          header {{ display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 18px 28px; background: #111827; color: #fff; }}
          header h1 {{ margin: 0; font-size: 20px; letter-spacing: 0; }}
          nav a {{ color: #cbd5e1; text-decoration: none; margin-left: 18px; font-weight: 700; }}
          nav a.active {{ color: #fff; }}
          main {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
          section {{ margin-bottom: 24px; }}
          h2 {{ font-size: 18px; margin: 0 0 14px; }}
          table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d9e0ea; }}
          th, td {{ padding: 11px 12px; border-bottom: 1px solid #e6ebf2; text-align: left; vertical-align: top; font-size: 14px; }}
          th {{ background: #edf2f7; color: #475569; font-size: 12px; text-transform: uppercase; }}
          small {{ display: block; color: #64748b; margin-top: 3px; }}
          form.settings {{ display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto; gap: 12px; align-items: end; background: #fff; border: 1px solid #d9e0ea; padding: 16px; }}
          label {{ display: grid; gap: 6px; font-size: 13px; color: #475569; font-weight: 700; }}
          input {{ border: 1px solid #cbd5e1; border-radius: 6px; padding: 9px 10px; font: inherit; }}
          button {{ border: 0; border-radius: 6px; padding: 8px 10px; background: #1f2937; color: #fff; font-weight: 700; cursor: pointer; }}
          button.danger {{ background: #b91c1c; }}
          .actions {{ display: grid; gap: 8px; min-width: 260px; }}
          .actions form {{ display: flex; flex-wrap: wrap; gap: 6px; }}
          .notice {{ padding: 11px 13px; border-radius: 6px; font-weight: 700; }}
          .success {{ background: #dcfce7; color: #166534; }}
          .error {{ background: #fee2e2; color: #991b1b; }}
          .empty {{ color: #64748b; text-align: center; padding: 24px; }}
          .badge {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #e2e8f0; font-weight: 700; }}
          .badge.active {{ background: #dcfce7; color: #166534; }}
          .badge.revoked {{ background: #fef3c7; color: #92400e; }}
          .badge.blocked {{ background: #fee2e2; color: #991b1b; }}
          @media (max-width: 900px) {{
            header {{ display: block; }}
            nav {{ margin-top: 12px; }}
            nav a {{ margin-left: 0; margin-right: 14px; }}
            main {{ padding: 14px; overflow-x: auto; }}
            form.settings {{ grid-template-columns: 1fr; }}
          }}
        </style>
      </head>
      <body>
        <header>
          <h1>SMS Gateway Admin</h1>
          <nav><a href="/admin/" {active}>Active</a><a href="/admin/archive" {archive}>Archive</a></nav>
        </header>
        <main>{content}</main>
      </body>
    </html>
    """
