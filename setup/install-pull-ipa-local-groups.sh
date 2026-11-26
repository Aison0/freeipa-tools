#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

ENV_NAME="pull-ipa-local-groups"
BIN_NAME="pull-ipa-local-groups.py"
SERVICE_NAME="pull-ipa-local-groups.service"
TIMER_NAME="pull-ipa-local-groups.timer"

SRC_ENV="$REPO_ROOT/config/$ENV_NAME"
SRC_BIN="$REPO_ROOT/bin/$BIN_NAME"
SRC_SERVICE="$REPO_ROOT/system.d/$SERVICE_NAME"
SRC_TIMER="$REPO_ROOT/system.d/$TIMER_NAME"

DST_BIN="/usr/local/bin/$BIN_NAME"
DST_SYSTEMD_DIR="/etc/systemd/system"
DST_SERVICE="$DST_SYSTEMD_DIR/$SERVICE_NAME"
DST_TIMER="$DST_SYSTEMD_DIR/$TIMER_NAME"
DST_ENV="/etc/default/pull-ipa-local-groups"

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: This script must be run as root." >&2
  exit 1
fi

for src in "$SRC_BIN" "$SRC_SERVICE" "$SRC_TIMER"; do
  if [ ! -f "$src" ]; then
    echo "Error: Required file '$src' not found." >&2
    exit 1
  fi
done

echo "Installing $BIN_NAME to $DST_BIN ..."
install -o root -g root -m 0755 "$SRC_BIN" "$DST_BIN"

echo "Installing systemd units to $DST_SYSTEMD_DIR ..."
install -o root -g root -m 0644 "$SRC_SERVICE" "$DST_SERVICE"
install -o root -g root -m 0644 "$SRC_TIMER" "$DST_TIMER"

if [ ! -e "$DST_ENV" ]; then
  echo "Creating default environment file at $DST_ENV ..."
  install -o root -g root -m 0600 "$SRC_ENV" "$DST_ENV"
else
  echo "Environment file '$DST_ENV' already exists - skipping."
fi

systemctl daemon-reload
systemctl enable --now "$TIMER_NAME"

echo "Installation complete."
echo "1. Edit $DST_ENV and set CONFIG_URL."
echo "2. Test manually with: $DST_BIN --dry-run"
echo "3. Trigger immediate sync: systemctl start $SERVICE_NAME"
