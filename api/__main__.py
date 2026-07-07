import uvicorn

from .config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    port = settings.app_port
    if settings.app_role == "portal":
        port = settings.hotspot_portal_port
    elif settings.app_role == "admin":
        port = settings.hotspot_admin_port
    uvicorn.run("api.app:app", host=settings.app_host, port=port)
