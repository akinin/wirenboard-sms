from dataclasses import dataclass
from typing import Optional

import httpx

from api.config import Settings


@dataclass
class UniFiClient:
    settings: Settings

    async def authorize_guest(
        self,
        client_mac: str,
        minutes: Optional[int] = None,
        ap_mac: Optional[str] = None,
    ) -> None:
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self.settings.unifi_username or not self.settings.unifi_password:
            raise RuntimeError("UNIFI_USERNAME and UNIFI_PASSWORD are required")

        auth_minutes = minutes or self.settings.unifi_auth_minutes
        async with httpx.AsyncClient(
            base_url=self.settings.unifi_base_url.rstrip("/"),
            verify=self.settings.unifi_verify_tls,
            timeout=15,
            follow_redirects=True,
        ) as client:
            await self._login(client)
            await self._post_unifi_command(
                client,
                "stamgr",
                _authorize_guest_payload(client_mac, auth_minutes, ap_mac),
            )

    async def find_client_mac_by_ip(self, client_ip: str) -> Optional[str]:
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self.settings.unifi_username or not self.settings.unifi_password:
            raise RuntimeError("UNIFI_USERNAME and UNIFI_PASSWORD are required")

        async with httpx.AsyncClient(
            base_url=self.settings.unifi_base_url.rstrip("/"),
            verify=self.settings.unifi_verify_tls,
            timeout=15,
            follow_redirects=True,
        ) as client:
            await self._login(client)
            data = await self._get_unifi_data(client, f"/proxy/network/api/s/{self.settings.unifi_site}/stat/sta")
            if data is None:
                data = await self._get_unifi_data(client, f"/api/s/{self.settings.unifi_site}/stat/sta")

        if not data:
            return None

        for item in data:
            if item.get("ip") == client_ip and item.get("mac"):
                return str(item["mac"])
        return None

    async def list_clients(self) -> list[dict[str, object]]:
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self.settings.unifi_username or not self.settings.unifi_password:
            raise RuntimeError("UNIFI_USERNAME and UNIFI_PASSWORD are required")

        async with httpx.AsyncClient(
            base_url=self.settings.unifi_base_url.rstrip("/"),
            verify=self.settings.unifi_verify_tls,
            timeout=15,
            follow_redirects=True,
        ) as client:
            await self._login(client)
            data = await self._get_unifi_data(client, f"/proxy/network/api/s/{self.settings.unifi_site}/stat/sta")
            if data is None:
                data = await self._get_unifi_data(client, f"/api/s/{self.settings.unifi_site}/stat/sta")
        return data or []

    async def is_guest_authorized(self, client_mac: str) -> Optional[bool]:
        client_mac = client_mac.lower()
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self.settings.unifi_username or not self.settings.unifi_password:
            raise RuntimeError("UNIFI_USERNAME and UNIFI_PASSWORD are required")

        async with httpx.AsyncClient(
            base_url=self.settings.unifi_base_url.rstrip("/"),
            verify=self.settings.unifi_verify_tls,
            timeout=15,
            follow_redirects=True,
        ) as client:
            await self._login(client)
            data = await self._get_unifi_data(client, f"/proxy/network/api/s/{self.settings.unifi_site}/stat/sta")
            if data is None:
                data = await self._get_unifi_data(client, f"/api/s/{self.settings.unifi_site}/stat/sta")

        if not data:
            return None

        for item in data:
            if str(item.get("mac", "")).lower() != client_mac:
                continue
            authorized = item.get("authorized")
            if isinstance(authorized, bool):
                return authorized
            if authorized is not None:
                return str(authorized).lower() in ("1", "true", "yes")
            if item.get("authorized_by") or item.get("guest_token"):
                return True
            return None
        return None

    async def unauthorize_guest(self, client_mac: str) -> None:
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self.settings.unifi_username or not self.settings.unifi_password:
            raise RuntimeError("UNIFI_USERNAME and UNIFI_PASSWORD are required")

        async with httpx.AsyncClient(
            base_url=self.settings.unifi_base_url.rstrip("/"),
            verify=self.settings.unifi_verify_tls,
            timeout=15,
            follow_redirects=True,
        ) as client:
            await self._login(client)
            await self._post_unifi_command(
                client,
                "stamgr",
                {"cmd": "unauthorize-guest", "mac": client_mac.lower()},
            )

    async def block_client(self, client_mac: str) -> None:
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self.settings.unifi_username or not self.settings.unifi_password:
            raise RuntimeError("UNIFI_USERNAME and UNIFI_PASSWORD are required")

        async with httpx.AsyncClient(
            base_url=self.settings.unifi_base_url.rstrip("/"),
            verify=self.settings.unifi_verify_tls,
            timeout=15,
            follow_redirects=True,
        ) as client:
            await self._login(client)
            await self._post_unifi_command(
                client,
                "stamgr",
                {"cmd": "block-sta", "mac": client_mac.lower()},
            )

    async def _login(self, client: httpx.AsyncClient) -> None:
        password = self.settings.unifi_password.get_secret_value()
        payload = {"username": self.settings.unifi_username, "password": password, "remember": True}

        response = await client.post("/api/auth/login", json=payload)
        if response.status_code == 404:
            response = await client.post("/api/login", json=payload)
        response.raise_for_status()
        if csrf_token := response.headers.get("x-csrf-token"):
            client.headers.update({"x-csrf-token": csrf_token})

    async def _post_unifi_command(
        self,
        client: httpx.AsyncClient,
        command_group: str,
        payload: dict[str, object],
    ) -> None:
        site = self.settings.unifi_site
        response = await client.post(f"/proxy/network/api/s/{site}/cmd/{command_group}", json=payload)
        if response.status_code == 404:
            response = await client.post(f"/api/s/{site}/cmd/{command_group}", json=payload)
        response.raise_for_status()
        data = response.json()
        if data.get("meta", {}).get("rc") not in (None, "ok"):
            raise RuntimeError(f"UniFi command failed: {data}")

    async def _get_unifi_data(
        self,
        client: httpx.AsyncClient,
        path: str,
    ) -> Optional[list[dict[str, object]]]:
        response = await client.get(path)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if payload.get("meta", {}).get("rc") not in (None, "ok"):
            raise RuntimeError(f"UniFi request failed: {payload}")
        data = payload.get("data")
        return data if isinstance(data, list) else None


def _authorize_guest_payload(
    client_mac: str,
    minutes: int,
    ap_mac: Optional[str],
) -> dict[str, object]:
    payload: dict[str, object] = {
        "cmd": "authorize-guest",
        "mac": client_mac.lower(),
        "minutes": minutes,
    }
    if ap_mac:
        payload["ap_mac"] = ap_mac.lower()
    return payload
