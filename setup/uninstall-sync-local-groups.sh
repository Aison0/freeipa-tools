#!/bin/sh
set -eu

BIN_NAME="sync-local-groups.py"
CONF_NAME="ipa-local-groups.conf"

DST_BIN="/usr/local/bin/$BIN_NAME"
DST_CONF="/etc/security/$CONF_NAME"
PAM_TARGET="${PAM_TARGET:-/etc/pam.d/common-session}"
PAM_MARKER="# sync-local-groups (pam_exec)"

PURGE_CONFIG=0
for arg in "$@"; do
  case "$arg" in
    --purge-config)
      PURGE_CONFIG=1
      ;;
    *)
      echo "Unbekannte Option: $arg" >&2
      echo "Verwendung: $0 [--purge-config]" >&2
      exit 1
      ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "Fehler: Dieses Skript muss als root ausgeführt werden." >&2
  exit 1
fi

if [ -f "$PAM_TARGET" ] && grep -Fq "$DST_BIN" "$PAM_TARGET"; then
  BACKUP_PAM="${PAM_TARGET}.bak.$(date +%Y%m%d%H%M%S)"
  cp -p "$PAM_TARGET" "$BACKUP_PAM"
  TMP_PAM="${PAM_TARGET}.tmp.$$"
  grep -Fv "$PAM_MARKER" "$PAM_TARGET" | grep -Fv "$DST_BIN" > "$TMP_PAM"
  cat "$TMP_PAM" > "$PAM_TARGET"
  rm -f "$TMP_PAM"
  echo "PAM-Eintrag aus '$PAM_TARGET' entfernt (Backup: $BACKUP_PAM)."
else
  echo "Kein PAM-Eintrag in '$PAM_TARGET' gefunden."
fi

if [ -e "$DST_BIN" ]; then
  rm -f "$DST_BIN"
  echo "Entfernt: $DST_BIN"
else
  echo "Nicht vorhanden: $DST_BIN"
fi

if [ -e "$DST_CONF" ]; then
  if [ "$PURGE_CONFIG" -eq 1 ]; then
    rm -f "$DST_CONF"
    echo "Konfiguration entfernt: $DST_CONF"
  else
    echo "Konfiguration '$DST_CONF' bleibt erhalten (zum Löschen '--purge-config' nutzen)."
  fi
fi

echo "Deinstallation abgeschlossen."
