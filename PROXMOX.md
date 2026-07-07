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
- `hotspot` - UniFi hotspot-портал на порту `8880`;
- `all` - оба сервиса.

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

## SMS backend

`install.sh` спросит, как отправлять SMS.

`mmcli` отправляет SMS напрямую через ModemManager:

```env
SMS_BACKEND=mmcli
MMCLI_MODEM_ID=auto
```

Для USB/LTE-модема внутри LXC нужно пробросить устройство с Proxmox host в
контейнер. В зависимости от модема privileged container может быть проще.

`mqtt` публикует запросы на отправку SMS в MQTT-топик:

```env
SMS_BACKEND=mqtt
WB_MQTT_HOST=127.0.0.1
WB_MQTT_PORT=1883
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on
```

Этот режим нужен, если SMS отправляет другой контроллер или отдельный сервис.

## После установки

Проверить API, если он установлен:

```bash
curl http://<container-ip>:8088/health
```

Проверить UniFi hotspot-портал, если он установлен:

```bash
curl http://<container-ip>:8880/
```

Логи внутри контейнера:

```bash
journalctl -u sms-gateway -f
journalctl -u sms-gateway-portal -f
```

Управление контейнером на Proxmox host:

```bash
pct status <CTID>
pct enter <CTID>
pct restart <CTID>
```
