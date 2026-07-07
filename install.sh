#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/akinin/wirenboard-sms.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/sms-gateway}"
SERVICE_USER="${SERVICE_USER:-root}"
COMPONENTS="${COMPONENTS:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo -E bash install.sh" >&2
  exit 1
fi

prompt() {
  local name="$1"
  local label="$2"
  local default="${3:-}"
  local value
  if [ -n "$default" ]; then
    read -r -p "$label [$default]: " value
    value="${value:-$default}"
  else
    read -r -p "$label: " value
  fi
  printf -v "$name" '%s' "$value"
}

prompt_secret() {
  local name="$1"
  local label="$2"
  local value
  read -r -s -p "$label: " value
  echo
  printf -v "$name" '%s' "$value"
}

prompt_choice() {
  local name="$1"
  local label="$2"
  local default="$3"
  local value
  while true; do
    read -r -p "$label [$default]: " value
    value="${value:-$default}"
    case "$value" in
      api|hotspot|all)
        printf -v "$name" '%s' "$value"
        return
        ;;
      *)
        echo "Choose one of: api, hotspot, all" >&2
        ;;
    esac
  done
}

component_enabled() {
  case "$COMPONENTS:$1" in
    all:*|api:api|hotspot:hotspot) return 0 ;;
    *) return 1 ;;
  esac
}

quote_env() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\'/\'\\\'\'}"
  printf "'%s'" "$value"
}

install_update_command() {
  cat > /usr/local/bin/sms-gateway-update <<EOF
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$INSTALL_DIR"

if [ "\$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo sms-gateway-update" >&2
  exit 1
fi

cd "\$INSTALL_DIR"
git pull --ff-only
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

systemctl daemon-reload
if systemctl is-enabled sms-gateway >/dev/null 2>&1; then
  systemctl restart sms-gateway
fi
if systemctl is-enabled sms-gateway-portal >/dev/null 2>&1; then
  systemctl restart sms-gateway-portal
fi

echo "SMS Gateway updated."
EOF
  chmod 755 /usr/local/bin/sms-gateway-update
}

install_motd() {
  cat > /etc/profile.d/sms-gateway-motd.sh <<EOF
#!/usr/bin/env bash
[ -n "\${PS1:-}" ] || return 0
[ "\${SMS_GATEWAY_MOTD_SHOWN:-0}" = "0" ] || return 0
export SMS_GATEWAY_MOTD_SHOWN=1

sms_gateway_ips="\$(hostname -I 2>/dev/null | xargs || true)"
sms_gateway_api_state="\$(systemctl is-active sms-gateway 2>/dev/null || true)"
sms_gateway_portal_state="\$(systemctl is-active sms-gateway-portal 2>/dev/null || true)"

cat <<'MOTD'

============================================================
 Wiren Board SMS Gateway
============================================================
MOTD
printf ' Hostname:      %s\n' "\$(hostname)"
printf ' IP address:    %s\n' "\${sms_gateway_ips:-unknown}"
cat <<'MOTD'
 GitHub:        https://github.com/akinin/wirenboard-sms
 Install dir:   $INSTALL_DIR
 Config:        $INSTALL_DIR/.env
 Data:          $INSTALL_DIR/data
 Update:        sms-gateway-update

 Services:
MOTD
printf '   sms-gateway         %s\n' "\${sms_gateway_api_state:-unknown}"
printf '   sms-gateway-portal  %s\n' "\${sms_gateway_portal_state:-unknown}"
cat <<'MOTD'

 Useful commands:
   nano $INSTALL_DIR/.env
   sms-gateway-update
   systemctl status sms-gateway
   systemctl status sms-gateway-portal
   journalctl -u sms-gateway -f
   journalctl -u sms-gateway-portal -f

============================================================

MOTD
EOF
  chmod 644 /etc/profile.d/sms-gateway-motd.sh
}

echo "Installing packages..."
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only
elif [ -d "$INSTALL_DIR" ] && [ "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 | wc -l)" -gt 0 ]; then
  echo "$INSTALL_DIR exists and is not empty. Move it away or set INSTALL_DIR." >&2
  exit 1
else
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if [ -n "${GITLAB_TOKEN:-}" ]; then
    git -c http.extraHeader="Authorization: Basic $(printf 'oauth2:%s' "$GITLAB_TOKEN" | base64 | tr -d '\n')" clone "$REPO_URL" "$INSTALL_DIR"
  else
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
fi

cd "$INSTALL_DIR"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

api_token="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
app_secret="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"

prompt app_host "APP_HOST" "0.0.0.0"
if [ -z "$COMPONENTS" ]; then
  prompt_choice COMPONENTS "Install components: api, hotspot or all" "all"
elif ! component_enabled api && ! component_enabled hotspot; then
  echo "Invalid COMPONENTS=$COMPONENTS. Use api, hotspot or all." >&2
  exit 1
fi
api_port="8088"
portal_port="8880"
if component_enabled api; then
  prompt api_port "Main API port" "8088"
fi
if component_enabled hotspot; then
  prompt portal_port "Portal port" "8880"
fi
prompt sms_backend "SMS backend: mqtt or mmcli" "mmcli"
prompt mmcli_modem_id "MMCLI modem id" "auto"
unifi_base_url=""
unifi_username=""
unifi_password=""
unifi_site="default"
unifi_verify_tls="false"
unifi_auth_minutes="1440"
telegram_chat_id=""
telegram_bot_token=""
if component_enabled hotspot; then
  prompt unifi_base_url "UniFi base URL" "https://10.10.1.1"
  prompt unifi_username "UniFi local username"
  prompt_secret unifi_password "UniFi local password"
  prompt unifi_site "UniFi site" "default"
  prompt unifi_verify_tls "Verify UniFi TLS: true or false" "false"
  prompt unifi_auth_minutes "Guest authorization minutes" "1440"
  prompt telegram_chat_id "Telegram chat id, blank to disable" ""
  if [ -n "$telegram_chat_id" ]; then
    prompt_secret telegram_bot_token "Telegram bot token"
  fi
fi
prompt otp_message_template "OTP SMS template" "Wi-Fi code: {code}"

if [ "$sms_backend" = "mmcli" ] && ! command -v mmcli >/dev/null 2>&1; then
  echo "Installing ModemManager for SMS_BACKEND=mmcli..."
  apt-get install -y modemmanager
fi

cat > .env <<EOF
# HTTP API
API_TOKEN=$api_token
APP_SECRET=$app_secret
APP_HOST=$app_host
APP_PORT=$api_port
DATABASE_PATH=./data/sms_gateway.sqlite3

# Wiren Board MQTT
WB_MQTT_HOST=127.0.0.1
WB_MQTT_PORT=1883
WB_MQTT_USERNAME=
WB_MQTT_PASSWORD=
WB_SMS_TOPIC=/devices/sms_sender/controls/send/on

# SMS backend
SMS_BACKEND=$sms_backend
MMCLI_MODEM_ID=$mmcli_modem_id

# UniFi Network
UNIFI_BASE_URL=$unifi_base_url
UNIFI_USERNAME=$unifi_username
UNIFI_PASSWORD=$(quote_env "$unifi_password")
UNIFI_SITE=$unifi_site
UNIFI_VERIFY_TLS=$unifi_verify_tls
UNIFI_AUTH_MINUTES=$unifi_auth_minutes

# Hotspot audit and Telegram notifications
HOTSPOT_ACCESS_LOG_PATH=./data/hotspot_access.csv
TELEGRAM_BOT_TOKEN=$(quote_env "$telegram_bot_token")
TELEGRAM_CHAT_ID=$telegram_chat_id

# OTP policy
OTP_TTL_SECONDS=300
OTP_LENGTH=6
OTP_RESEND_SECONDS=60
OTP_MAX_ATTEMPTS=5
OTP_MESSAGE_TEMPLATE=$(quote_env "$otp_message_template")
EOF

install -d -m 755 data

cp deploy/sms-gateway.service /etc/systemd/system/sms-gateway.service
cp deploy/sms-gateway-portal.service /etc/systemd/system/sms-gateway-portal.service
sed -i "s#WorkingDirectory=/opt/sms-gateway#WorkingDirectory=$INSTALL_DIR#g" /etc/systemd/system/sms-gateway.service /etc/systemd/system/sms-gateway-portal.service
sed -i "s#EnvironmentFile=/opt/sms-gateway/.env#EnvironmentFile=$INSTALL_DIR/.env#g" /etc/systemd/system/sms-gateway.service /etc/systemd/system/sms-gateway-portal.service
sed -i "s#ExecStart=/opt/sms-gateway/.venv/bin/python#ExecStart=$INSTALL_DIR/.venv/bin/python#g" /etc/systemd/system/sms-gateway.service /etc/systemd/system/sms-gateway-portal.service
sed -i "s#Environment=APP_PORT=8880#Environment=APP_PORT=$portal_port#g" /etc/systemd/system/sms-gateway-portal.service
sed -i "s#User=root#User=$SERVICE_USER#g" /etc/systemd/system/sms-gateway.service /etc/systemd/system/sms-gateway-portal.service

systemctl daemon-reload
if component_enabled api; then
  systemctl enable --now sms-gateway
  systemctl restart sms-gateway
else
  systemctl disable --now sms-gateway 2>/dev/null || true
fi
if component_enabled hotspot; then
  systemctl enable --now sms-gateway-portal
  systemctl restart sms-gateway-portal
else
  systemctl disable --now sms-gateway-portal 2>/dev/null || true
fi

install_update_command
install_motd

echo
echo "Installed."
echo "Components: $COMPONENTS"
echo "Config: $INSTALL_DIR/.env"
echo "Update: sms-gateway-update"
if component_enabled api; then
  echo "API:    http://<controller>:$api_port/health"
fi
if component_enabled hotspot; then
  echo "Portal: http://<controller>:$portal_port/"
fi
echo "Audit:  $INSTALL_DIR/data/hotspot_access.csv"
