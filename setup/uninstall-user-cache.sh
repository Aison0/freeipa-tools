#!/usr/bin/env bash
# Description:
# Removes the installed user-cache wrapper and Python script. Optionally
# removes the cache base directory when --purge-cache-dir is passed.

set -euo pipefail

ADMIN_CACHE_BASE="${ADMIN_CACHE_BASE:-/var/cache/users}"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/bin}"
PROFILE_D_DIR="${PROFILE_D_DIR:-/etc/profile.d}"
PURGE_CACHE_DIR=false

for arg in "$@"; do
  case "$arg" in
    --purge-cache-dir)
      PURGE_CACHE_DIR=true
      ;;
    *)
      echo "Error: Unknown option '$arg'" >&2
      echo "Usage: $0 [--purge-cache-dir]" >&2
      exit 1
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Error: This uninstallation script must be run as root (e.g. via sudo)." >&2
  exit 1
fi

DEST_PYTHON_SCRIPT="${INSTALL_BIN_DIR}/setup-user-cache.py"
DEST_PROFILE_SCRIPT="${PROFILE_D_DIR}/user-cache.sh"

echo "Removing ${DEST_PROFILE_SCRIPT}..."
rm -f "${DEST_PROFILE_SCRIPT}"

echo "Removing ${DEST_PYTHON_SCRIPT}..."
rm -f "${DEST_PYTHON_SCRIPT}"

if [[ "${PURGE_CACHE_DIR}" == true ]]; then
  echo "Removing cache base directory ${ADMIN_CACHE_BASE}..."
  rm -rf "${ADMIN_CACHE_BASE}"
fi

echo "Uninstallation completed successfully."
