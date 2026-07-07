# Установка в Proxmox VE LXC

Запустите на Proxmox VE host под `root`:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/akinin/wirenboard-sms/main/proxmox-install.sh)"
```

Скрипт создает Debian LXC контейнер через `pct`, запускает его, устанавливает
проект внутри контейнера, создает `.env` и включает выбранные systemd-сервисы.

## Компоненты

Установщик спросит, что установить:

- `api` - HTTP API на порту `8088`;
- `hotspot` - UniFi hotspot-портал на порту `8880` и админка на порту `8089`;
- `all` - API, UniFi hotspot-портал и админка.

Админку можно закрыть firewall-ом от гостевой сети, оставив гостям доступ
только к порту `8880`.

## Дефолтные параметры LXC

Если просто нажимать Enter, будут использованы:

- Debian: `13`
- CPU: `1` core
- RAM: `512` MB
- disk: `8` GB
- hostname: `sms-gateway`
- network bridge: `vmbr0`
- IPv4: `dhcp`
- template storage: `local`
- rootfs storage: `local-lvm`
- container type: unprivileged
- autostart: enabled
- features: `nesting=1`

Если на Proxmox host пока нет шаблона Debian 13, в поле
`Debian LXC template version` можно ввести `12`.

## Логин и пароль

Дефолтного пароля нет.

Установщик спросит:

```text
Root password for the container, blank to skip
```

Если ввести пароль, он будет установлен для `root`.

Если оставить поле пустым, вход выполняется с Proxmox host:

```bash
pct enter <CTID>
```

SSH-сервер отдельно не устанавливается.

## Админка

Админка доступна на отдельном порту:

```text
http://<container-ip>:8089/admin/
```

В ней можно:

- просматривать активных клиентов;
- видеть телефон, MAC, IP и live-информацию из UniFi;
- видеть RX/TX трафик, если UniFi возвращает эти поля;
- продлевать авторизацию на `1`, `2`, `7`, `14`, `30`, `365` дней;
- отзывать авторизацию;
- блокировать клиента;
- смотреть архив ранее авторизованных клиентов;
- менять логотип портала;
- менять надпись `Welcome to Olshaniki`.

## SMS backend

`install.sh` спросит, как отправлять SMS. По умолчанию используется `mqtt`,
потому что основной сценарий - SMS отправляет отдельный Wiren Board.

`mqtt` публикует запросы на отправку SMS в MQTT-топик Wiren Board:

```env
SMS_BACKEND=mqtt
WB_MQTT_HOST=<ip-wiren-board>
WB_MQTT_PORT=1883
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on
```

Используйте этот режим, если модем подключен к Wiren Board или SMS отправляет
другой сервис, доступный через MQTT.

`mmcli` отправляет SMS напрямую через ModemManager и подходит только если
GSM/LTE-модем доступен прямо внутри LXC контейнера на Proxmox:

```env
SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
```

Для USB/LTE-модема внутри LXC нужно пробросить устройство с Proxmox host в
контейнер. В зависимости от модема privileged container может быть проще.

## После установки

Проверить API, если он установлен:

```bash
curl http://<container-ip>:8088/health
```

Проверить UniFi hotspot-портал, если он установлен:

```bash
curl http://<container-ip>:8880/
```

Проверить админку, если hotspot установлен:

```bash
curl http://<container-ip>:8089/admin/
```

Логи внутри контейнера:

```bash
journalctl -u sms-gateway -f
journalctl -u sms-gateway-portal -f
journalctl -u sms-gateway-admin -f
```

Управление контейнером на Proxmox host:

```bash
pct status <CTID>
pct enter <CTID>
pct restart <CTID>
```
