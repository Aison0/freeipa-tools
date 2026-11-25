#!/usr/bin/env bash
# Description:
# Installs setup-user-cache.py and its /etc/profile.d wrapper script,
# sets proper permissions, and prepares the local user cache base directory.

set -euo pipefail

REMOTE_HOME_PREFIX="${REMOTE_HOME_PREFIX:-/home/remote/}"
ADMIN_CACHE_BASE="${ADMIN_CACHE_BASE:-/var/cache/users}"
INSTALL_BIN_DIR="${INSTALL_BIN_DIR:-/usr/local/bin}"
PROFILE_D_DIR="${PROFILE_D_DIR:-/etc/profile.d}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SRC_PYTHON_SCRIPT="${REPO_ROOT}/bin/setup-user-cache.py"
SRC_PROFILE_SCRIPT="${REPO_ROOT}/profile.d/user-cache.sh"

DEST_PYTHON_SCRIPT="${INSTALL_BIN_DIR}/setup-user-cache.py"
DEST_PROFILE_SCRIPT="${PROFILE_D_DIR}/user-cache.sh"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Error: This installation script must be run as root (e.g. via sudo)." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required but not installed." >&2
  exit 1
fi

if [[ ! -f "${SRC_PYTHON_SCRIPT}" ]] || [[ ! -f "${SRC_PROFILE_SCRIPT}" ]]; then
  echo "Error: Required source files not found." >&2
  echo "Expected: ${SRC_PYTHON_SCRIPT} and ${SRC_PROFILE_SCRIPT}" >&2
  exit 1
fi

echo "Creating cache base directory at ${ADMIN_CACHE_BASE}..."
mkdir -p "${ADMIN_CACHE_BASE}"
chown root:root "${ADMIN_CACHE_BASE}"
chmod 1777 "${ADMIN_CACHE_BASE}"

echo "Installing ${DEST_PYTHON_SCRIPT}..."
install -o root -g root -m 0755 "${SRC_PYTHON_SCRIPT}" "${DEST_PYTHON_SCRIPT}"

echo "Installing ${DEST_PROFILE_SCRIPT}..."
install -o root -g root -m 0644 "${SRC_PROFILE_SCRIPT}" "${DEST_PROFILE_SCRIPT}"

sed -i \
  -e "s|^REMOTE_HOME_PREFIX=.*|REMOTE_HOME_PREFIX=\"${REMOTE_HOME_PREFIX}\"|" \
  -e "s|^ADMIN_CACHE_BASE=.*|ADMIN_CACHE_BASE=\"${ADMIN_CACHE_BASE}\"|" \
  -e "s|^SETUP_CACHE_SCRIPT=.*|SETUP_CACHE_SCRIPT=\"${DEST_PYTHON_SCRIPT}\"|" \
  "${DEST_PROFILE_SCRIPT}"

echo "Installation completed successfully."
echo "Changes will take effect for new login shells."
