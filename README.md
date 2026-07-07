# Wirenboard SMS Gateway

HTTP API для отправки SMS и одноразовых кодов, а также опциональный UniFi
hotspot-портал с авторизацией гостей по SMS.

Основной вариант установки - Debian LXC контейнер на Proxmox VE. По умолчанию
установщик создает контейнер на Debian 13.

## Установка в Proxmox LXC

Запустите на Proxmox VE host под `root`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/akinin/wirenboard-sms/main/proxmox-install.sh)"
```

Установщик создаст Debian LXC контейнер и запустит `install.sh` внутри него.
Во время установки можно выбрать состав:

- `api` - только HTTP API.
- `hotspot` - только UniFi hotspot-портал.
- `all` - оба сервиса.

Дефолтные параметры LXC:

- Debian: `13`
- CPU: `1` core
- RAM: `512` MB
- disk: `8` GB
- hostname: `sms-gateway`
- bridge: `vmbr0`
- IPv4: `dhcp`
- container type: unprivileged
- autostart: enabled

Дефолтного пароля у контейнера нет. Установщик может задать пароль `root`, если
ввести его при установке. Если оставить пароль пустым, вход выполняется с
Proxmox host:

```bash
pct enter <CTID>
```

## Ручная установка

Этот раздел нужен, если Debian 13 или Debian 12 контейнер/сервер уже создан и
проект нужно поставить вручную.

Установите системные пакеты:

```bash
apt update
apt install -y python3 python3-venv python3-pip git curl
```

Склонируйте проект:

```bash
git clone https://github.com/akinin/wirenboard-sms.git /opt/sms-gateway
cd /opt/sms-gateway
```

Создайте виртуальное окружение и установите зависимости:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Создайте `.env`:

```bash
cp .env.example .env
nano .env
```

Сгенерируйте локальные секреты:

```bash
python3 - <<'PY'
import secrets
print("API_TOKEN=" + secrets.token_urlsafe(32))
print("APP_SECRET=" + secrets.token_urlsafe(48))
PY
```

Вставьте полученные значения в `.env`.

Установите systemd units:

```bash
cp deploy/sms-gateway.service /etc/systemd/system/
cp deploy/sms-gateway-portal.service /etc/systemd/system/
systemctl daemon-reload
```

Включите только нужный сервис:

```bash
# Только API
systemctl enable --now sms-gateway

# Только hotspot-портал
systemctl enable --now sms-gateway-portal

# Оба сервиса
systemctl enable --now sms-gateway sms-gateway-portal
```

## SMS backend

Проект умеет отправлять SMS двумя способами.

`SMS_BACKEND=mmcli` отправляет SMS напрямую через ModemManager:

```env
SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
```

Если используется этот режим, установите ModemManager:

```bash
apt install -y modemmanager
```

Для USB/LTE-модема внутри LXC нужно пробросить устройство с Proxmox host в
контейнер. В зависимости от модема и настроек хоста privileged container может
быть проще.

`SMS_BACKEND=mqtt` публикует запросы на отправку SMS в MQTT-топик:

```env
SMS_BACKEND=mqtt
WB_MQTT_HOST=127.0.0.1
WB_MQTT_PORT=1883
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on
```

Этот режим удобен, если SMS отправляет другой контроллер или отдельный сервис.

## API

API-сервис слушает `APP_PORT`, по умолчанию `8088`.

Минимальные значения `.env`:

```env
API_TOKEN=change-me
APP_SECRET=change-me-to-a-long-random-string
APP_HOST=0.0.0.0
APP_PORT=8088
DATABASE_PATH=./data/sms_gateway.sqlite3
SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
OTP_MESSAGE_TEMPLATE=Your code: {code}
```

Проверка:

```bash
curl http://<container-ip>:8088/health
```

Отправить SMS:

```bash
curl -X POST http://<container-ip>:8088/api/sms \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","message":"Test SMS"}'
```

Запросить OTP:

```bash
curl -X POST http://<container-ip>:8088/api/otp/request \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","purpose":"test"}'
```

Проверить OTP:

```bash
curl -X POST http://<container-ip>:8088/api/otp/verify \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","purpose":"test","code":"123456"}'
```

Логи:

```bash
journalctl -u sms-gateway -f
```

## Hotspot

Hotspot-сервис по умолчанию слушает порт `8880` и обслуживает UniFi guest
portal. Он отправляет SMS-код, проверяет его и авторизует гостевого клиента в
UniFi Network.

Минимальные значения `.env`:

```env
API_TOKEN=change-me
APP_SECRET=change-me-to-a-long-random-string
APP_HOST=0.0.0.0
APP_PORT=8088
DATABASE_PATH=./data/sms_gateway.sqlite3

SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
OTP_MESSAGE_TEMPLATE=Wi-Fi code: {code}

UNIFI_BASE_URL=https://10.10.1.1
UNIFI_USERNAME=local-unifi-admin
UNIFI_PASSWORD=local-unifi-password
UNIFI_SITE=default
UNIFI_VERIFY_TLS=false
UNIFI_AUTH_MINUTES=1440

HOTSPOT_ACCESS_LOG_PATH=./data/hotspot_access.csv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Для UniFi OS `UNIFI_BASE_URL` обычно выглядит как
`https://<unifi-address>:443`. Для self-hosted UniFi Network controller часто
используется `https://<unifi-address>:8443`. Нужен локальный UniFi admin без
MFA.

В UniFi Network:

- включите external portal;
- укажите IP LXC контейнера;
- добавьте IP LXC контейнера в Pre-Authorization Access / Walled Garden.

Должны открываться:

```text
http://<container-ip>:8880/
http://<container-ip>:8880/guest/s/default/
http://<container-ip>:8880/guest/s/default/login
http://<container-ip>:8880/portal/
```

Сервис принимает стандартные параметры UniFi portal: `id`, `ap`, `url`.
Также поддерживаются варианты `mac`, `client_mac`, `clientmac`, `sta`,
`client`, `redirect_url`, `redirect`.

Hotspot API endpoints:

```text
POST /api/hotspot/request-code
POST /api/hotspot/verify-code
```

Запросить hotspot-код:

```bash
curl -X POST http://<container-ip>:8088/api/hotspot/request-code \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","client_mac":"aa:bb:cc:dd:ee:ff","redirect_url":"https://example.com"}'
```

Проверить hotspot-код:

```bash
curl -X POST http://<container-ip>:8088/api/hotspot/verify-code \
  -H "Authorization: Bearer <API_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","client_mac":"aa:bb:cc:dd:ee:ff","code":"123456"}'
```

Журнал входов:

```text
/opt/sms-gateway/data/hotspot_access.csv
```

Формат CSV:

```text
date,time,mac,phone,valid_until
```

Логи:

```bash
journalctl -u sms-gateway-portal -f
```
