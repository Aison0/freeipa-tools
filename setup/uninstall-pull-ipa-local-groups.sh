#!/bin/sh
set -eu

BIN_NAME="pull-ipa-local-groups.py"
SERVICE_NAME="pull-ipa-local-groups.service"
TIMER_NAME="pull-ipa-local-groups.timer"

DST_BIN="/usr/local/bin/$BIN_NAME"
DST_SYSTEMD_DIR="/etc/systemd/system"
DST_SERVICE="$DST_SYSTEMD_DIR/$SERVICE_NAME"
DST_TIMER="$DST_SYSTEMD_DIR/$TIMER_NAME"
DST_ENV="/etc/default/pull-ipa-local-groups"

PURGE_CONFIG=0
for arg in "$@"; do
  case "$arg" in
    --purge-config)
      PURGE_CONFIG=1
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--purge-config]" >&2
      exit 1
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Error: This script must be run as root." >&2
  exit 1
fi

if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled --quiet "$TIMER_NAME" 2>/dev/null || systemctl is-active --quiet "$TIMER_NAME" 2>/dev/null; then
    echo "Stopping and disabling $TIMER_NAME ..."
    systemctl disable --now "$TIMER_NAME" >/dev/null 2>&1 || true
  fi
  if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "Stopping $SERVICE_NAME ..."
    systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  fi
fi

for unit_file in "$DST_TIMER" "$DST_SERVICE"; do
  if [ -e "$unit_file" ]; then
    rm -f "$unit_file"
    echo "Removed: $unit_file"
  fi
done

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl reset-failed "$SERVICE_NAME" "$TIMER_NAME" >/dev/null 2>&1 || true
fi

if [ -e "$DST_BIN" ]; then
  rm -f "$DST_BIN"
  echo "Removed: $DST_BIN"
else
  echo "Not found: $DST_BIN"
fi

if [ -e "$DST_ENV" ]; then
  if [ "$PURGE_CONFIG" -eq 1 ]; then
    rm -f "$DST_ENV"
    echo "Removed environment file: $DST_ENV"
  else
    echo "Environment file '$DST_ENV' kept (use '--purge-config' to remove)."
  fi
fi

echo "Uninstallation complete."
