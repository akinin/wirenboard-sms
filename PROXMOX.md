# Proxmox VE LXC install

Этот способ запускается на самом Proxmox VE host под `root`. Скрипт создает
Debian LXC через `pct`, запускает контейнер и устанавливает SMS Gateway
внутри него.

## Публичный GitHub

После публикации репозитория в GitHub установка будет выглядеть так:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/akinin/wirenboard-sms/main/proxmox-install.sh)"
```

Во время установки можно выбрать:

- `api` - только HTTP API на порту `8088`;
- `hotspot` - только UniFi hotspot portal на порту `8880`;
- `all` - оба сервиса.

Скрипт также спросит параметры LXC: CTID, hostname, версию Debian, storage,
bridge, IP/DHCP, CPU, memory, disk size и тип контейнера.

## Дефолтные параметры LXC

Если просто нажимать Enter, установщик предложит:

- Debian: `13`;
- CPU: `1` core;
- RAM: `512` MB;
- disk: `8` GB;
- hostname: `sms-gateway`;
- network bridge: `vmbr0`;
- IPv4: `dhcp`;
- template storage: `local`;
- rootfs storage: `local-lvm`;
- container type: unprivileged;
- onboot: enabled;
- features: `nesting=1`.

Если на вашем Proxmox пока нет шаблона Debian 13, можно ввести `12` в поле
`Debian LXC template version`.

## Логин и пароль контейнера

Дефолтного пароля нет. Установщик спросит `Root password for the container,
blank to skip`:

- если ввести пароль, он будет установлен для `root`;
- если оставить пустым, пароль `root` не задается, а вход делается с Proxmox host:

```bash
pct enter <CTID>
```

SSH-сервер отдельно не ставится.

## Текущий приватный GitLab

Пока проект лежит в приватном GitLab, передайте token только через переменную
окружения. Он нужен для скачивания скрипта и для `git clone` внутри LXC.

```bash
read -rsp 'GitLab token: ' GITLAB_TOKEN; echo
export GITLAB_TOKEN
export INSTALL_SCRIPT_URL='https://git.akinin.su/akininav/sms/-/raw/main/install.sh'
export REPO_URL='https://git.akinin.su/akininav/sms.git'
curl -fsSL --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  https://git.akinin.su/akininav/sms/-/raw/main/proxmox-install.sh \
  | bash
unset GITLAB_TOKEN
```

## SMS backend

`install.sh` спросит SMS backend:

- `mmcli` - отправка напрямую через ModemManager. Для USB/LTE-модема в LXC
  обычно потребуется проброс устройства в контейнер; иногда проще выбрать
  privileged container.
- `mqtt` - совместимость со старой схемой через MQTT, например если SMS по-прежнему
  отправляет отдельный Wiren Board или другой MQTT-сервис.

Если выбран `mmcli`, установщик внутри контейнера поставит `modemmanager`.

## После установки

Проверка API:

```bash
curl http://<container-ip>:8088/health
```

Проверка UniFi hotspot portal:

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
