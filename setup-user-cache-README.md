# setup-user-cache

Moves `$HOME/.cache` off NFS onto a local filesystem for users under a configured remote home prefix and exports `XDG_CACHE_HOME` during login.

## Repository Layout

* `bin/setup-user-cache.py` — Main Python script resolving the local cache path and repairing/creating the `$HOME/.cache` symlink
* `profile.d/user-cache.sh` — Login shell wrapper for `/etc/profile.d` exporting `XDG_CACHE_HOME`
* `setup/install-user-cache.sh` — Installer script
* `setup/uninstall-user-cache.sh` — Uninstaller script
* `setup-user-cache.README.md` — Documentation for this tool

## Installation

Run the installer as `root`:

```bash
chmod +x ./setup/install-user-cache.sh ./setup/uninstall-user-cache.sh
sudo ./setup/install-user-cache.sh
```

Optional environment variables during installation:

```bash
sudo \
  REMOTE_HOME_PREFIX="/nfs/home/" \
  ADMIN_CACHE_BASE="/var/cache/users" \
  ./setup/install-user-cache.sh
```

The installer:
1. Creates `ADMIN_CACHE_BASE` with mode `1777` (`root:root`) so users can create their own subdirectory while sticky-bit protection prevents deleting others' directories.
2. Installs `setup-user-cache.py` to `/usr/local/bin/setup-user-cache.py` (`0755`).
3. Installs `user-cache.sh` to `/etc/profile.d/user-cache.sh` (`0644`) and writes the configured prefix and cache base path into it.

## Usage

On login shells where `$HOME` starts with `REMOTE_HOME_PREFIX`, `/etc/profile.d/user-cache.sh` executes `setup-user-cache.py` and exports `XDG_CACHE_HOME`.

You can also invoke `setup-user-cache.py` directly:

```bash
/usr/local/bin/setup-user-cache.py \
  --remote-home-prefix /home/remote/ \
  --admin-cache-base /var/cache/users
```

### CLI Arguments

* `--remote-home-prefix` (required): Only act when `$HOME` starts with this prefix.
* `--admin-cache-base` (required): Preferred local base directory; the script attempts `<admin-cache-base>/<username>` and verifies directory ownership (`UID`) and write access.
* `--fallback-cache-base` (optional, repeatable): Fallback base directories used if the preferred directory cannot be created, is not owned by the current UID, or is not writable. Defaults to `/var/tmp` and `/tmp`.

## NFS & Symlink Handling

* **Existing directories or files at `$HOME/.cache`:** Renamed to `.cache.stale.<pid>.<timestamp>` before cleanup so active `.nfs*` files do not block replacing `$HOME/.cache` with a symlink.
* **Broken or mismatched symlinks:** Unlinked and recreated pointing to the resolved local cache directory (`0700` on fallback directories).

## Uninstallation

```bash
sudo ./setup/uninstall-user-cache.sh
```

To also remove the cache base directory:

```bash
sudo ./setup/uninstall-user-cache.sh --purge-cache-dir
```
