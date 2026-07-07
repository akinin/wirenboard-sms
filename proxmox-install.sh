#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="sms-gateway"
INSTALL_SCRIPT_URL="${INSTALL_SCRIPT_URL:-https://raw.githubusercontent.com/akinin/wirenboard-sms/main/install.sh}"
REPO_URL="${REPO_URL:-https://github.com/akinin/wirenboard-sms.git}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run on the Proxmox VE host as root." >&2
  exit 1
fi

if ! command -v pct >/dev/null 2>&1; then
  echo "pct was not found. Run this script on a Proxmox VE host." >&2
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

prompt_secret_optional() {
  local name="$1"
  local label="$2"
  local value
  read -r -s -p "$label, blank to skip: " value
  echo
  printf -v "$name" '%s' "$value"
}

prompt_component() {
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
        echo "Invalid choice." >&2
        ;;
    esac
  done
}

prompt_yes_no() {
  local name="$1"
  local label="$2"
  local default="$3"
  local value
  while true; do
    read -r -p "$label [$default]: " value
    value="${value:-$default}"
    case "$value" in
      yes|no)
        printf -v "$name" '%s' "$value"
        return
        ;;
      *)
        echo "Choose yes or no." >&2
        ;;
    esac
  done
}

next_ctid() {
  if command -v pvesh >/dev/null 2>&1; then
    pvesh get /cluster/nextid
  else
    local id=100
    while pct status "$id" >/dev/null 2>&1; do
      id=$((id + 1))
    done
    echo "$id"
  fi
}

latest_debian_template() {
  local storage="$1"
  local version="$2"
  local template
  pveam update >/dev/null
  template="$(pveam available --section system | awk -v pattern="debian-${version}-standard" '$2 ~ pattern {print $2}' | tail -n 1)"
  if [ -z "$template" ]; then
    echo "Cannot find a Debian $version LXC template in pveam." >&2
    exit 1
  fi
  if ! pveam list "$storage" | awk '{print $1}' | grep -qx "${storage}:vztmpl/${template}"; then
    pveam download "$storage" "$template"
  fi
  echo "${storage}:vztmpl/${template}"
}

echo "SMS Gateway Proxmox LXC installer"
echo

default_ctid="$(next_ctid)"
prompt CTID "Container ID" "$default_ctid"
if pct status "$CTID" >/dev/null 2>&1; then
  echo "Container $CTID already exists." >&2
  exit 1
fi

prompt HOSTNAME "Container hostname" "$PROJECT_NAME"
prompt DEBIAN_VERSION "Debian LXC template version" "13"
prompt TEMPLATE_STORAGE "Template storage" "local"
prompt ROOTFS_STORAGE "Rootfs storage" "local-lvm"
prompt DISK_SIZE "Disk size in GB" "8"
prompt MEMORY "Memory in MB" "512"
prompt CORES "CPU cores" "1"
prompt BRIDGE "Network bridge" "vmbr0"
prompt IP_CONFIG "IPv4 config: dhcp or CIDR, for example 10.10.100.5/24" "dhcp"
GATEWAY=""
if [ "$IP_CONFIG" != "dhcp" ]; then
  prompt GATEWAY "IPv4 gateway" ""
fi
prompt DNS_SERVER "DNS server, blank for Proxmox default" ""
prompt_yes_no UNPRIVILEGED "Create unprivileged container: yes or no" "yes"
prompt_component COMPONENTS "Install components: api, hotspot or all" "all"
prompt_secret_optional ROOT_PASSWORD "Root password for the container"

template_ref="$(latest_debian_template "$TEMPLATE_STORAGE" "$DEBIAN_VERSION")"

net0="name=eth0,bridge=${BRIDGE},ip=${IP_CONFIG}"
if [ -n "$GATEWAY" ]; then
  net0="${net0},gw=${GATEWAY}"
fi

create_args=(
  "$CTID"
  "$template_ref"
  --hostname "$HOSTNAME"
  --cores "$CORES"
  --memory "$MEMORY"
  --rootfs "${ROOTFS_STORAGE}:${DISK_SIZE}"
  --net0 "$net0"
  --features "nesting=1"
  --onboot 1
  --start 1
)

if [ "$UNPRIVILEGED" = "yes" ]; then
  create_args+=(--unprivileged 1)
else
  create_args+=(--unprivileged 0)
fi

if [ -n "$DNS_SERVER" ]; then
  create_args+=(--nameserver "$DNS_SERVER")
fi

echo
echo "Creating LXC container $CTID..."
pct create "${create_args[@]}"

if [ -n "$ROOT_PASSWORD" ]; then
  pct exec "$CTID" -- env ROOT_PASSWORD="$ROOT_PASSWORD" bash -lc \
    'printf "root:%s\n" "$ROOT_PASSWORD" | chpasswd'
fi

echo "Waiting for container network..."
network_ready="no"
for _ in $(seq 1 30); do
  if pct exec "$CTID" -- bash -lc "getent hosts deb.debian.org >/dev/null 2>&1"; then
    network_ready="yes"
    break
  fi
  sleep 2
done
if [ "$network_ready" != "yes" ]; then
  echo "Container network is not ready. Check bridge/IP/DNS settings for CT $CTID." >&2
  exit 1
fi

echo "Installing base tools in the container..."
pct exec "$CTID" -- bash -lc "apt-get update && apt-get install -y ca-certificates curl bash"

echo "Downloading project installer in the container..."
if [ -n "${GITLAB_TOKEN:-}" ]; then
  pct exec "$CTID" -- env GITLAB_TOKEN="$GITLAB_TOKEN" INSTALL_SCRIPT_URL="$INSTALL_SCRIPT_URL" bash -lc \
    'curl -fsSL --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" "${INSTALL_SCRIPT_URL}" -o /tmp/sms-gateway-install.sh'
else
  pct exec "$CTID" -- env INSTALL_SCRIPT_URL="$INSTALL_SCRIPT_URL" bash -lc \
    'curl -fsSL "${INSTALL_SCRIPT_URL}" -o /tmp/sms-gateway-install.sh'
fi
pct exec "$CTID" -- chmod +x /tmp/sms-gateway-install.sh

echo
echo "Running project installer inside the container..."
pct exec "$CTID" -- env \
  REPO_URL="$REPO_URL" \
  GITLAB_TOKEN="${GITLAB_TOKEN:-}" \
  COMPONENTS="$COMPONENTS" \
  bash /tmp/sms-gateway-install.sh

echo
echo "Done."
echo "Container: $CTID ($HOSTNAME)"
echo "Components: $COMPONENTS"
echo "API health, if installed: http://<container-ip>:8088/health"
echo "Hotspot portal, if installed: http://<container-ip>:8880/"
