#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BIN_NAME="sync-local-groups.py"
CONF_NAME="ipa-local-groups.conf"

SRC_BIN="${REPO_ROOT}/bin/$BIN_NAME"
SRC_CONF="${REPO_ROOT}/config/$CONF_NAME"

DST_BIN="/usr/local/bin/$BIN_NAME"
DST_CONF_DIR="/etc/security"
DST_CONF="$DST_CONF_DIR/$CONF_NAME"

PAM_TARGET="${PAM_TARGET:-/etc/pam.d/common-session}"
PAM_MARKER="# sync-local-groups (pam_exec)"
PAM_LINE="session optional pam_exec.so $DST_BIN"

if [ "$(id -u)" -ne 0 ]; then
  echo "Fehler: Dieses Skript muss als root ausgeführt werden." >&2
  exit 1
fi

for req in usermod gpasswd python3; do
  if ! command -v "$req" >/dev/null 2>&1; then
    echo "Warnung: Benötigtes Kommando '$req' wurde im PATH nicht gefunden." >&2
  fi
done

if [ ! -f "$SRC_BIN" ]; then
  echo "Fehler: '$SRC_BIN' nicht gefunden." >&2
  exit 1
fi

echo "Installiere $BIN_NAME nach $DST_BIN ..."
install -o root -g root -m 0755 "$SRC_BIN" "$DST_BIN"

install -d -o root -g root -m 0755 "$DST_CONF_DIR"
if [ -f "$SRC_CONF" ]; then
  if [ -e "$DST_CONF" ]; then
    echo "Konfiguration '$DST_CONF' existiert bereits – wird nicht überschrieben."
    echo "  (Neue Vorlage liegt unter: $SRC_CONF)"
  else
    echo "Installiere Standardkonfiguration nach $DST_CONF ..."
    install -o root -g root -m 0644 "$SRC_CONF" "$DST_CONF"
  fi
fi

if [ -f "$PAM_TARGET" ]; then
  if grep -Fq "$DST_BIN" "$PAM_TARGET"; then
    echo "PAM-Eintrag in '$PAM_TARGET' ist bereits vorhanden."
  else
    BACKUP_PAM="${PAM_TARGET}.bak.$(date +%Y%m%d%H%M%S)"
    cp -p "$PAM_TARGET" "$BACKUP_PAM"
    printf '\n%s\n%s\n' "$PAM_MARKER" "$PAM_LINE" >> "$PAM_TARGET"
    echo "PAM-Eintrag zu '$PAM_TARGET' hinzugefügt (Backup: $BACKUP_PAM)."
  fi
else
  echo "Hinweis: PAM-Datei '$PAM_TARGET' nicht gefunden."
  echo "Bitte manuell ergänzen (z. B. mit PAM_TARGET=/etc/pam.d/system-auth aufrufen):"
  echo "  $PAM_LINE"
fi

echo "Installation abgeschlossen."
echo "Testlauf mit: $DST_BIN -n -u <benutzername>"
