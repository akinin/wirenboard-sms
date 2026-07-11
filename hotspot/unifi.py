from dataclasses import dataclass
from typing import Optional

import httpx

from api.config import Settings


class UniFiClientNotFoundError(RuntimeError):
    pass


@dataclass
class UniFiClient:
    settings: Settings

    @property
    def _uses_api_key(self) -> bool:
        return bool(
            self.settings.unifi_api_key
            and self.settings.unifi_api_key.get_secret_value().strip()
        )

    def _check_configuration(self) -> None:
        if not self.settings.unifi_base_url:
            raise RuntimeError("UNIFI_BASE_URL is not configured")
        if not self._uses_api_key and (
            not self.settings.unifi_username or not self.settings.unifi_password
        ):
            raise RuntimeError(
                "Set UNIFI_API_KEY (recommended) or UNIFI_USERNAME and UNIFI_PASSWORD"
            )

    def _client_options(self) -> dict[str, object]:
        headers = {"Accept": "application/json"}
        if self._uses_api_key:
            headers["X-API-Key"] = self.settings.unifi_api_key.get_secret_value()
        return {
            "base_url": self.settings.unifi_base_url.rstrip("/"),
            "verify": self.settings.unifi_verify_tls,
            "timeout": 15,
            "follow_redirects": True,
            "headers": headers,
        }

    async def authorize_guest(
        self,
        client_mac: str,
        minutes: Optional[int] = None,
        ap_mac: Optional[str] = None,
    ) -> None:
        self._check_configuration()

        auth_minutes = minutes or self.settings.unifi_auth_minutes
        async with httpx.AsyncClient(**self._client_options()) as client:
            if self._uses_api_key:
                await self._integration_client_action(
                    client,
                    client_mac,
                    {"action": "AUTHORIZE_GUEST_ACCESS", "timeLimitMinutes": auth_minutes},
                )
                return
            await self._login(client)
            await self._post_unifi_command(
                client,
                "stamgr",
                _authorize_guest_payload(client_mac, auth_minutes, ap_mac),
            )

    async def find_client_mac_by_ip(self, client_ip: str) -> Optional[str]:
        self._check_configuration()
        async with httpx.AsyncClient(**self._client_options()) as client:
            if self._uses_api_key:
                clients = await self._integration_clients(client, ip_address=client_ip)
                return str(clients[0]["macAddress"]).lower() if clients else None
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
        self._check_configuration()
        async with httpx.AsyncClient(**self._client_options()) as client:
            if self._uses_api_key:
                return [self._legacy_client_shape(item) for item in await self._integration_clients(client)]
            await self._login(client)
            data = await self._get_unifi_data(client, f"/proxy/network/api/s/{self.settings.unifi_site}/stat/sta")
            if data is None:
                data = await self._get_unifi_data(client, f"/api/s/{self.settings.unifi_site}/stat/sta")
        return data or []

    async def is_guest_authorized(self, client_mac: str) -> Optional[bool]:
        client_mac = client_mac.lower()
        self._check_configuration()
        async with httpx.AsyncClient(**self._client_options()) as client:
            if self._uses_api_key:
                clients = await self._integration_clients(client, mac_address=client_mac)
                if not clients:
                    return None
                authorized = (clients[0].get("access") or {}).get("authorized")
                return authorized if isinstance(authorized, bool) else None
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
        self._check_configuration()
        async with httpx.AsyncClient(**self._client_options()) as client:
            if self._uses_api_key:
                await self._integration_client_action(
                    client, client_mac, {"action": "UNAUTHORIZE_GUEST_ACCESS"}
                )
                return
            await self._login(client)
            await self._post_unifi_command(
                client,
                "stamgr",
                {"cmd": "unauthorize-guest", "mac": client_mac.lower()},
            )

    async def block_client(self, client_mac: str) -> None:
        self._check_configuration()
        if self._uses_api_key:
            raise RuntimeError(
                "Blocking clients is not supported by the official UniFi Integration API"
            )

        async with httpx.AsyncClient(**self._client_options()) as client:
            await self._login(client)
            await self._post_unifi_command(
                client,
                "stamgr",
                {"cmd": "block-sta", "mac": client_mac.lower()},
            )

    async def _integration_site_id(self, client: httpx.AsyncClient) -> str:
        response = await client.get("/proxy/network/integration/v1/sites", params={"limit": 100})
        self._raise_integration_error(response)
        sites = response.json().get("data", [])
        configured = self.settings.unifi_site.lower()
        for site in sites:
            values = (site.get("id"), site.get("internalReference"), site.get("name"))
            if any(str(value).lower() == configured for value in values if value is not None):
                return str(site["id"])
        available = ", ".join(str(site.get("name") or site.get("internalReference")) for site in sites)
        raise RuntimeError(
            f"UniFi site '{self.settings.unifi_site}' not found"
            + (f"; available: {available}" if available else "")
        )

    async def _integration_clients(
        self,
        client: httpx.AsyncClient,
        mac_address: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> list[dict[str, object]]:
        site_id = await self._integration_site_id(client)
        params: dict[str, object] = {"limit": 100}
        if mac_address:
            params["filter"] = f"macAddress.eq('{mac_address.lower()}')"
        elif ip_address:
            params["filter"] = f"ipAddress.eq('{ip_address}')"
        path = f"/proxy/network/integration/v1/sites/{site_id}/clients"
        response = await client.get(path, params=params)
        self._raise_integration_error(response)
        data = response.json().get("data", [])
        clients = data if isinstance(data, list) else []

        # Some UniFi Network releases compare MAC filters case-sensitively even
        # though MAC addresses themselves are case-insensitive. If the filtered
        # request found nothing, fetch the connected clients and compare locally.
        if mac_address and not clients:
            response = await client.get(path, params={"limit": 100})
            self._raise_integration_error(response)
            data = response.json().get("data", [])
            if isinstance(data, list):
                expected = mac_address.lower()
                clients = [
                    item
                    for item in data
                    if str(item.get("macAddress", "")).lower() == expected
                ]
        return clients

    async def _integration_client_action(
        self,
        client: httpx.AsyncClient,
        client_mac: str,
        payload: dict[str, object],
    ) -> None:
        clients = await self._integration_clients(client, mac_address=client_mac)
        if not clients:
            raise UniFiClientNotFoundError(f"UniFi client {client_mac.lower()} not found")
        site_id = await self._integration_site_id(client)
        response = await client.post(
            f"/proxy/network/integration/v1/sites/{site_id}/clients/{clients[0]['id']}/actions",
            json=payload,
        )
        self._raise_integration_error(response)

    @staticmethod
    def _raise_integration_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"UniFi API key was rejected (HTTP {response.status_code}); check the key and permissions"
            )
        if response.status_code == 404:
            raise RuntimeError(
                "Official UniFi Integration API is unavailable; update UniFi Network or use a local account"
            )
        response.raise_for_status()

    @staticmethod
    def _legacy_client_shape(item: dict[str, object]) -> dict[str, object]:
        access = item.get("access") if isinstance(item.get("access"), dict) else {}
        return {
            **item,
            "mac": item.get("macAddress"),
            "ip": item.get("ipAddress"),
            "authorized": access.get("authorized"),
        }

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
