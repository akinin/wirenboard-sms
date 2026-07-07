from dataclasses import dataclass
import re
import subprocess

from .config import Settings


@dataclass
class SmsSender:
    settings: Settings

    def send(self, phone: str, message: str) -> None:
        if self.settings.sms_backend == "mmcli":
            self._send_via_mmcli(phone, message)
            return
        if self.settings.sms_backend != "mqtt":
            raise RuntimeError(f"Unsupported SMS_BACKEND: {self.settings.sms_backend}")
        self._send_via_mqtt(phone, message)

    def _send_via_mqtt(self, phone: str, message: str) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is not installed") from exc

        payload = f"{phone};{message}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        if self.settings.wb_mqtt_username:
            password = (
                self.settings.wb_mqtt_password.get_secret_value()
                if self.settings.wb_mqtt_password
                else None
            )
            client.username_pw_set(self.settings.wb_mqtt_username, password)

        client.connect(self.settings.wb_mqtt_host, self.settings.wb_mqtt_port, keepalive=10)
        result = client.publish(self.settings.wb_sms_topic, payload=payload, qos=1)
        result.wait_for_publish(timeout=10)
        client.disconnect()

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed with code {result.rc}")

    def _send_via_mmcli(self, phone: str, message: str) -> None:
        modem_id = self._resolve_mmcli_modem_id()
        sms_properties = f'number={phone},text="{self._escape_mmcli_value(message)}"'
        create_result = subprocess.run(
            ["mmcli", "-m", modem_id, f"--messaging-create-sms={sms_properties}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if create_result.returncode != 0:
            error = create_result.stderr.strip() or create_result.stdout.strip()
            if "couldn't find modem" in error and modem_id != "any":
                modem_id = self._resolve_mmcli_modem_id(force_auto=True)
                create_result = subprocess.run(
                    ["mmcli", "-m", modem_id, f"--messaging-create-sms={sms_properties}"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if create_result.returncode == 0:
                    error = ""
            if error:
                raise RuntimeError(error)

        match = re.search(r"(/org/freedesktop/ModemManager1/SMS/\d+)", create_result.stdout)
        if not match:
            raise RuntimeError(f"Cannot find created SMS path in mmcli output: {create_result.stdout}")

        sms_path = match.group(1)
        send_result = subprocess.run(
            ["mmcli", "-s", sms_path, "--send"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if send_result.returncode != 0:
            raise RuntimeError(send_result.stderr.strip() or send_result.stdout.strip())

    def _escape_mmcli_value(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _resolve_mmcli_modem_id(self, force_auto: bool = False) -> str:
        configured = self.settings.mmcli_modem_id
        if configured and configured not in ("any", "auto") and not force_auto:
            return configured

        list_result = subprocess.run(
            ["mmcli", "-L"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if list_result.returncode != 0:
            raise RuntimeError(list_result.stderr.strip() or list_result.stdout.strip())

        match = re.search(r"/org/freedesktop/ModemManager1/Modem/(\d+)", list_result.stdout)
        if not match:
            raise RuntimeError(f"Cannot find modem in mmcli output: {list_result.stdout}")
        return match.group(1)
