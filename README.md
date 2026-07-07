# SMS Gateway

Proxmox VE LXC installer: see `PROXMOX.md`.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/akinin/wirenboard-sms/main/proxmox-install.sh)"
```

Нейтральный HTTP API для отправки SMS и одноразовых кодов через Wiren Board. Сейчас в проекте есть отдельный модуль `hotspot` для авторизации гостей UniFi по SMS, но само API можно использовать и в других проектах: Home Assistant, мониторинг, внутренние скрипты, уведомления.

Проект совместим с Python 3.9, который обычно стоит в Debian на Wiren Board 8.x.

## Структура

```text
api/       базовое SMS/OTP API
hotspot/   UniFi hotspot: портал, проверка SMS-кода, authorize-guest
deploy/    systemd unit для автозапуска
```

SMS отправляются тем же способом, что уже настроен на Wiren Board: публикацией в MQTT-топик `/devices/sms_sender/controls/send/on` в формате `+79991234567;Текст`.

## Быстрая установка на новый Wiren Board

Если репозиторий приватный, создайте GitLab token с правом чтения репозитория и передайте его только в переменную окружения. Токен не записывается в конфиг проекта.

```bash
read -rsp 'GitLab token: ' GITLAB_TOKEN; echo
export GITLAB_TOKEN
curl -fsSL --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  https://git.akinin.su/akininav/sms/-/raw/main/install.sh \
  | sudo -E bash
unset GITLAB_TOKEN
```

Установщик:

- ставит системные пакеты, виртуальное окружение и зависимости;
- клонирует или обновляет проект в `/opt/sms-gateway`;
- спрашивает UniFi URL, локального пользователя UniFi, Telegram chat id/token, SMS backend и время авторизации;
- создаёт `.env` с локальными секретами;
- включает `sms-gateway` и `sms-gateway-portal` в systemd.

После установки проверьте:

```bash
curl http://127.0.0.1:8088/health
curl http://127.0.0.1:8880/
journalctl -u sms-gateway-portal -f
```

Журнал входов пишется в:

```text
/opt/sms-gateway/data/hotspot_access.csv
```

Формат CSV:

```text
date,time,mac,phone,valid_until
```

## Ручная установка на Wiren Board / Debian

Команды ниже рассчитаны на установку в `/opt/sms-gateway`.

1. Установить системные пакеты:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

2. Скопировать проект на контроллер.

Если проект уже лежит на вашем компьютере, проще передать папку по `scp`:

```bash
scp -r /path/to/sms root@wirenboard:/opt/sms-gateway
```

Если будете хранить проект в git-репозитории, можно вместо этого сделать:

```bash
sudo git clone <repo-url> /opt/sms-gateway
```

3. Создать виртуальное окружение и поставить зависимости:

```bash
cd /opt/sms-gateway
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

4. Создать конфиг:

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

`API_TOKEN` нужен для доступа к HTTP API. `APP_SECRET` используется для подписи одноразовых SMS-кодов, его нельзя менять после запуска, если в базе ещё есть активные коды.

Минимально поменяйте в `.env`:

```env
API_TOKEN=значение-из-команды-выше
APP_SECRET=значение-из-команды-выше
WB_MQTT_HOST=127.0.0.1
WB_MQTT_PORT=1883
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
SMS_BACKEND=mqtt
MMCLI_MODEM_ID=auto
OTP_MESSAGE_TEMPLATE=Your Wi-Fi code: {code}
APP_PORT=8088
```

Для `SMS_BACKEND=mmcli` лучше оставить OTP-текст латиницей. На некоторых модемах кириллический текст создаётся в ModemManager, но отправка падает с ошибкой `Unknown`.

Если сервис запускается прямо на Wiren Board, `WB_MQTT_HOST=127.0.0.1` обычно оставляют как есть. Если сервис будет на другом сервере, укажите IP-адрес Wiren Board.

`SMS_BACKEND=mqtt` использует текущее правило Wiren Board из `/etc/wb-rules/send_sms.js`.

Если MQTT/правило видит сообщение, но SMS не доставляются, можно отправлять напрямую через ModemManager:

```env
SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
```

Если на контроллере несколько модемов, посмотрите ID:

```bash
mmcli -L
```

И укажите нужный, например:

```env
MMCLI_MODEM_ID=2
```

Проверить, где слушает MQTT:

```bash
sudo ss -ltnp | grep ':1883'
```

Посмотреть конфиги MQTT:

```bash
sudo ls -la /etc/mosquitto /etc/mosquitto/conf.d
sudo grep -RniE 'listener|port|allow_anonymous|password_file|cafile|certfile|keyfile' /etc/mosquitto
```

Если в конфиге есть `allow_anonymous true` или нет `password_file`, логин и пароль обычно не нужны:

```env
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
```

Если есть строка `password_file ...`, путь к файлу с пользователями будет указан рядом. Например:

```bash
sudo grep -Rni 'password_file' /etc/mosquitto
sudo cat /путь/из/password_file
```

В `passwd` видны имена пользователей, но не исходные пароли. Если пароль неизвестен, проще создать отдельного пользователя для сервиса:

```bash
sudo mosquitto_passwd /путь/из/password_file sms_gateway
sudo systemctl restart mosquitto
```

Ключ `-c` используйте только если файла пользователей ещё нет: он создаёт новый файл и может затереть старый.

После этого укажите:

```env
WB_MQTT_USERNAME=sms_gateway
WB_MQTT_PASSWORD=пароль-который-ввели
```

Проверить публикацию в MQTT можно так:

```bash
sudo apt install -y mosquitto-clients
mosquitto_pub -h 127.0.0.1 -p 1883 \
  -t /devices/sms_sender/controls/send/on \
  -m '+79991234567;Проверка MQTT SMS'
```

Если MQTT требует логин и пароль:

```bash
mosquitto_pub -h 127.0.0.1 -p 1883 \
  -u sms_gateway -P 'пароль' \
  -t /devices/sms_sender/controls/send/on \
  -m '+79991234567;Проверка MQTT SMS'
```

Для UniFi hotspot также заполните:

```env
UNIFI_BASE_URL=https://адрес-unifi-controller
UNIFI_USERNAME=пользователь-unifi
UNIFI_PASSWORD=пароль-unifi
UNIFI_SITE=default
UNIFI_VERIFY_TLS=false
```

Если у UniFi самоподписанный сертификат, оставьте `UNIFI_VERIFY_TLS=false`.

Для журнала входов и Telegram-уведомлений заполните:

```env
HOTSPOT_ACCESS_LOG_PATH=./data/hotspot_access.csv
TELEGRAM_BOT_TOKEN=токен-бота
TELEGRAM_CHAT_ID=id-чата
```

После успешной авторизации сервис записывает дату, время, MAC, телефон и срок действия в CSV-файл, а также отправляет сообщение в Telegram. Ошибка Telegram не блокирует доступ к Wi-Fi.

Для iPhone и простого SMS лучше держать шаблон коротким:

```env
OTP_MESSAGE_TEMPLATE=Wi-Fi code: {code}
```

5. Проверить ручной запуск:

```bash
cd /opt/sms-gateway
. .venv/bin/activate
python -m api
```

Сервис должен слушать `0.0.0.0:8088`. В другом окне можно проверить:

```bash
curl http://127.0.0.1:8088/health
```

6. Включить автозапуск:

```bash
sudo cp /opt/sms-gateway/deploy/sms-gateway.service /etc/systemd/system/
sudo cp /opt/sms-gateway/deploy/sms-gateway-portal.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sms-gateway
sudo systemctl enable --now sms-gateway-portal
sudo systemctl status sms-gateway
```

Логи:

```bash
journalctl -u sms-gateway -f
journalctl -u sms-gateway-portal -f
```

## API

Проверка:

```bash
curl http://wirenboard:8088/health
```

Отправить SMS:

```bash
curl -X POST http://wirenboard:8088/api/sms \
  -H "Authorization: Bearer long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","message":"Проверка SMS API"}'
```

Также поддерживается короткий вариант без `Bearer`:

```bash
curl -X POST http://wirenboard:8088/api/sms \
  -H "Authorization: long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","message":"Проверка SMS API"}'
```

Запросить OTP:

```bash
curl -X POST http://wirenboard:8088/api/otp/request \
  -H "Authorization: Bearer long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","purpose":"test"}'
```

Проверить OTP:

```bash
curl -X POST http://wirenboard:8088/api/otp/verify \
  -H "Authorization: Bearer long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","purpose":"test","code":"123456"}'
```

## UniFi hotspot

В UniFi Network включите внешний портал. Если поле принимает только IPv4-адрес, укажите без схемы, порта и пути:

```text
10.10.100.5
```

Для этого сценария используется второй systemd-сервис `sms-gateway-portal`, который слушает стандартный гостевой HTTP-порт `8880`. Должны открываться оба адреса:

```text
http://10.10.100.5:8880/
http://10.10.100.5:8880/guest/s/default/
http://10.10.100.5:8880/guest/s/default/login
http://10.10.100.5:8880/portal/
```

Сервис принимает стандартные параметры UniFi: `id` как MAC клиента, `ap` как MAC точки доступа и `url` как адрес возврата. Также поддерживаются частые варианты `mac`, `client_mac`, `clientmac`, `sta`, `client`, `redirect_url` и `redirect`.

В Pre-Authorization Access / Walled Garden добавьте IP `10.10.100.5`, чтобы гости могли открыть портал до авторизации. Если гостевой клиент не может открыть портал, сначала проверьте именно доступность порта `8880` из гостевой сети.

Быстрая диагностика на Wiren Board:

```bash
sudo systemctl status sms-gateway-portal
sudo ss -ltnp | grep ':8880'
curl -i 'http://127.0.0.1:8880/guest/s/default/?id=aa:bb:cc:dd:ee:ff&ap=11:22:33:44:55:66&url=https://example.com'
journalctl -u sms-gateway-portal -f
```

Для `UNIFI_BASE_URL` используйте адрес самого UniFi Network Controller/Console, а не адрес портала. Для UniFi OS обычно это `https://адрес-unifi:443`, для self-hosted controller часто `https://адрес-unifi:8443`, для UniFi OS Server часто `https://адрес-unifi:11443`. Пользователь UniFi должен быть локальным администратором без MFA/2FA.

Для сценария без HTML-портала есть отдельные endpoint'ы:

```text
POST /api/hotspot/request-code
POST /api/hotspot/verify-code
```

Пример:

```bash
curl -X POST http://wirenboard:8088/api/hotspot/request-code \
  -H "Authorization: Bearer long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","client_mac":"aa:bb:cc:dd:ee:ff","redirect_url":"https://example.com"}'
```

```bash
curl -X POST http://wirenboard:8088/api/hotspot/verify-code \
  -H "Authorization: Bearer long-random-token" \
  -H "Content-Type: application/json" \
  -d '{"phone":"+79991234567","client_mac":"aa:bb:cc:dd:ee:ff","code":"123456"}'
```

## Дальше

- Добавить rate limit по IP и телефону перед публикацией портала в гостевую сеть.
- Добавить отдельный `notify`-пример для Home Assistant поверх `/api/sms`.
- Позже вынести Home Assistant в отдельный модуль, как сейчас вынесен `hotspot`.
