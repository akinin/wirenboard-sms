# Wiren Board SMS Gateway

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
- `hotspot` - UniFi hotspot-портал и админка.
- `all` - API, UniFi hotspot-портал и админка.

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

Порты по умолчанию:

- API: `8088`
- hotspot-портал для гостей: `8880`
- админка: `8089`

Админку можно закрыть firewall-ом от гостевой сети, оставив гостям доступ
только к `8880`.

Дефолтного пароля у контейнера нет. Установщик может задать пароль `root`, если
ввести его при установке. Если оставить пароль пустым, вход выполняется с
Proxmox host:

```bash
pct enter <CTID>
```

При входе в контейнер показывается приветственное окно с hostname, IP-адресом,
ссылкой на GitHub, путями к файлам, статусом сервисов и полезными командами.

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

Сгенерируйте `API_TOKEN` и `APP_SECRET`:

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
cp deploy/sms-gateway-admin.service /etc/systemd/system/
systemctl daemon-reload
```

## Управление сервисами

API:

```bash
systemctl enable --now sms-gateway
systemctl start sms-gateway
systemctl stop sms-gateway
systemctl restart sms-gateway
systemctl status sms-gateway
journalctl -u sms-gateway -f
```

Hotspot-портал:

```bash
systemctl enable --now sms-gateway-portal
systemctl enable --now sms-gateway-admin
systemctl start sms-gateway-portal
systemctl start sms-gateway-admin
systemctl stop sms-gateway-portal
systemctl stop sms-gateway-admin
systemctl restart sms-gateway-portal
systemctl restart sms-gateway-admin
systemctl status sms-gateway-portal
systemctl status sms-gateway-admin
journalctl -u sms-gateway-portal -f
journalctl -u sms-gateway-admin -f
```

Все сервисы:

```bash
systemctl enable --now sms-gateway sms-gateway-portal sms-gateway-admin
systemctl restart sms-gateway sms-gateway-portal sms-gateway-admin
systemctl status sms-gateway
systemctl status sms-gateway-portal
systemctl status sms-gateway-admin
```

Отключить автозапуск:

```bash
systemctl disable --now sms-gateway
systemctl disable --now sms-gateway-portal
systemctl disable --now sms-gateway-admin
```

## Обновление

Автоматический установщик создает команду:

```bash
sms-gateway-update
```

Она выполняет `git pull --ff-only`, обновляет Python-зависимости и перезапускает
включенные сервисы.

Если проект установлен вручную и команды еще нет, обновить можно так:

```bash
cd /opt/sms-gateway
git pull --ff-only
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
systemctl restart sms-gateway sms-gateway-portal
```

## Приветственное окно контейнера

Автоматический установщик создает файл:

```text
/etc/profile.d/sms-gateway-motd.sh
```

При входе в контейнер он показывает:

- hostname и IP контейнера;
- ссылку на GitHub;
- путь установки `/opt/sms-gateway`;
- путь к конфигу `/opt/sms-gateway/.env`;
- путь к данным `/opt/sms-gateway/data`;
- команду обновления `sms-gateway-update`;
- статус `sms-gateway` и `sms-gateway-portal`;
- статус `sms-gateway-admin`;
- команды для просмотра логов и управления сервисами.

## SMS backend

Проект умеет отправлять SMS двумя способами. Основной сценарий для этого
проекта - отдельный Wiren Board отправляет SMS, а LXC контейнер публикует
запрос в MQTT.

`SMS_BACKEND=mqtt` публикует запросы на отправку SMS в MQTT-топик Wiren Board:

```env
SMS_BACKEND=mqtt
WB_MQTT_HOST=<ip-wiren-board>
WB_MQTT_PORT=1883
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on
```

Используйте этот режим, если модем подключен к Wiren Board или SMS отправляет
другой сервис, доступный через MQTT.

`SMS_BACKEND=mmcli` нужен только если GSM/LTE-модем доступен прямо внутри LXC
контейнера на Proxmox:

```env
SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
```

Если используется `mmcli`, установите ModemManager:

```bash
apt install -y modemmanager
```

Для USB/LTE-модема внутри LXC нужно пробросить устройство с Proxmox host в
контейнер. В зависимости от модема и настроек хоста privileged container может
быть проще.

## API

API-сервис слушает `APP_PORT`, по умолчанию `8088`.

Получите значения `API_TOKEN` и `APP_SECRET` командой:

```bash
python3 - <<'PY'
import secrets
print("API_TOKEN=" + secrets.token_urlsafe(32))
print("APP_SECRET=" + secrets.token_urlsafe(48))
PY
```

Минимальные значения `.env`:

```env
API_TOKEN=<значение-из-команды-выше>
APP_SECRET=<значение-из-команды-выше>
APP_HOST=0.0.0.0
APP_PORT=8088
DATABASE_PATH=./data/sms_gateway.sqlite3
SMS_BACKEND=mqtt
WB_MQTT_HOST=<ip-wiren-board>
WB_MQTT_PORT=1883
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on
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

## Hotspot

Hotspot-сервис по умолчанию слушает порт `8880` и обслуживает UniFi guest
portal. Он отправляет SMS-код, проверяет его и авторизует гостевого клиента в
UniFi Network.

Получите значения `API_TOKEN` и `APP_SECRET` командой:

```bash
python3 - <<'PY'
import secrets
print("API_TOKEN=" + secrets.token_urlsafe(32))
print("APP_SECRET=" + secrets.token_urlsafe(48))
PY
```

Минимальные значения `.env`:

```env
API_TOKEN=<значение-из-команды-выше>
APP_SECRET=<значение-из-команды-выше>
APP_HOST=0.0.0.0
APP_PORT=8088
DATABASE_PATH=./data/sms_gateway.sqlite3

SMS_BACKEND=mqtt
WB_MQTT_HOST=<ip-wiren-board>
WB_MQTT_PORT=1883
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on
MMCLI_MODEM_ID=auto
OTP_MESSAGE_TEMPLATE=Wi-Fi code: {code}

UNIFI_BASE_URL=https://10.10.1.1
UNIFI_API_KEY=<network-integration-api-key>
UNIFI_USERNAME=
UNIFI_PASSWORD=
UNIFI_SITE=default
UNIFI_VERIFY_TLS=false
UNIFI_AUTH_MINUTES=1440

HOTSPOT_PORTAL_PORT=8880
HOTSPOT_ADMIN_PORT=8089
HOTSPOT_PORTAL_TITLE=Welcome to Olshaniki
HOTSPOT_LOGO_PATH=./data/hotspot_logo.png

HOTSPOT_ACCESS_LOG_PATH=./data/hotspot_access.csv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Рекомендуемый способ — API-ключ из `UniFi Network → Control Plane → Integrations`.
При заданном `UNIFI_API_KEY` логин и пароль не используются. Требуется UniFi Network
с официальным Integration API (9.1.105 или новее).

Для UniFi OS `UNIFI_BASE_URL` обычно выглядит как
`https://<unifi-address>:443`. Для self-hosted UniFi Network controller часто
используется `https://<unifi-address>:8443`. Для старых версий без Integration API
можно оставить `UNIFI_API_KEY` пустым и указать локального UniFi admin без MFA в
`UNIFI_USERNAME` и `UNIFI_PASSWORD`.

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

Админка открывается отдельно:

```text
http://<container-ip>:8089/admin/
```

В админке есть:

- список активных клиентов;
- телефон, MAC, IP и live-информация из UniFi, если контроллер ее отдает;
- трафик RX/TX, если UniFi возвращает эти поля;
- продление авторизации на `1`, `2`, `7`, `14`, `30`, `365` дней;
- отзыв авторизации;
- блокировка клиента через UniFi `block-sta`;
- вкладка `Archive` с ранее авторизованными клиентами;
- тестовая отправка SMS из админки;
- замена логотипа портала;
- замена надписи `Welcome to Olshaniki`.
- переключение языка админки `RU / EN`.

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
