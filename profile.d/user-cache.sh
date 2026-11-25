#!/bin/sh
# Description:
# Runs at login for users whose home directory matches a configured remote
# prefix. Invokes setup-user-cache.py with the required arguments to point
# ~/.cache to a local cache directory and exports the resolved path as
# XDG_CACHE_HOME in the shell environment.

REMOTE_HOME_PREFIX="/home/remote/"
ADMIN_CACHE_BASE="/var/cache/users"
SETUP_CACHE_SCRIPT="/usr/local/bin/setup-user-cache.py"

if [ "${HOME#"$REMOTE_HOME_PREFIX"}" != "$HOME" ]; then
  if CACHE_DIR=$("$SETUP_CACHE_SCRIPT" \
    --remote-home-prefix "$REMOTE_HOME_PREFIX" \
    --admin-cache-base "$ADMIN_CACHE_BASE" \
    2>/dev/null) && [ -n "$CACHE_DIR" ]; then
    export XDG_CACHE_HOME="$CACHE_DIR"
  fi
fi
