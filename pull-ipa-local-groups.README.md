# pull-ipa-local-groups

Lightweight pull mechanism to centrally distribute and validate `/etc/security/ipa-local-groups.conf` across Linux hosts managed with `sync-local-groups.py`.

## Repository Structure

```text
.
├── bin/
│   └── pull-ipa-local-groups.py            # Python fetcher, validator & atomic writer
├── system.d/
│   ├── pull-ipa-local-groups.service       # systemd oneshot service
│   └── pull-ipa-local-groups.timer         # systemd periodic timer
├── config/
│   └── pull-ipa-local-groups               # configuration file for /etc/default
├── setup/
│   └── install-pull-ipa-local-groups.sh    # Installation script
│   └── uninstall-pull-ipa-local-groups.sh  # Uninstallation script
└── pull-ipa-local-groups.README.md         # Documentation
```

## How It Works

1. **Fetch:** Periodically triggered via `pull-ipa-local-groups.timer`, `pull-ipa-local-groups.py` downloads the central `ipa-local-groups.conf` over HTTPS (using only the Python 3 standard library).
2. **Validate:** Verifies UTF-8 encoding, ensures the file is non-empty, and checks that every non-comment rule line matches `<user> <ipa-group> <local-groups>`.
3. **Atomic Update:** Only writes if the content has changed. Replaces `/etc/security/ipa-local-groups.conf` atomically (`os.replace`) with permissions `0644` (`root:root`) so PAM logins (`sync-local-groups.py`) never read partial files or stall on network outages.
4. **Audit:** Logs updates and errors to `syslog` (`LOG_INFO` / `LOG_ERR`).

## Installation

```sh
chmod +x setup/install-pull-ipa-local-groups.sh
sudo ./setup/install-pull-ipa-local-groups.sh
```

The installer:
- Copies `bin/pull-ipa-local-groups.py` to `/usr/local/bin/pull-ipa-local-groups.py` (`0755`).
- Installs `pull-ipa-local-groups.service` and `pull-ipa-local-groups.timer` into `/etc/systemd/system/`.
- Creates `/etc/default/pull-ipa-local-groups` (`0600`) if it does not exist yet.
- Reloads `systemd` and enables `pull-ipa-local-groups.timer`.

## Configuration

Edit `/etc/default/pull-ipa-local-groups`:

```sh
CONFIG_URL="https://config.example.internal/ipa-local-groups.conf"
# AUTH_HEADER="Bearer your-secret-token"
# CA_BUNDLE="/etc/ipa/ca.crt"
# PULL_TIMEOUT="20"
```

### CLI Options

`pull-ipa-local-groups.py` can also be invoked directly for testing or custom overrides:

```text
usage: pull-ipa-local-groups.py [-h] [-u URL] [-o OUTPUT] [-H AUTH_HEADER]
                                [--ca-bundle CA_BUNDLE] [-t TIMEOUT] [-n]
```

* `-u, --url` — HTTPS URL of the central configuration (defaults to `CONFIG_URL`).
* `-o, --output` — Destination path (defaults to `/etc/security/ipa-local-groups.conf`).
* `-H, --auth-header` — Optional HTTP `Authorization` header (defaults to `AUTH_HEADER`).
* `--ca-bundle` — Path to a custom CA certificate bundle (e.g., `/etc/ipa/ca.crt` for FreeIPA CAs).
* `-t, --timeout` — Request timeout in seconds (default: `20`).
* `-n, --dry-run` — Fetch and validate without writing changes to disk.

## Testing & Verification

1. **Dry-run validation:**
   ```sh
   sudo /usr/local/bin/pull-ipa-local-groups.py --url https://config.example.internal/ipa-local-groups.conf --dry-run
   ```
2. **Trigger an immediate pull via systemd:**
   ```sh
   sudo systemctl start pull-ipa-local-groups.service
   journalctl -u pull-ipa-local-groups.service -n 20
   ```
3. **Check timer schedule:**
   ```sh
   systemctl list-timers pull-ipa-local-groups.timer
   ```
4. **Verify effective group rules with `sync-local-groups.py`:**
   ```sh
   sudo /usr/local/bin/sync-local-groups.py -n -u <username>
   ```

## Uninstallation

To remove `pull-ipa-local-groups` and its systemd units, run:

```sh
chmod +x setup/uninstall-pull-ipa-local-groups.sh
sudo ./setup/uninstall-pull-ipa-local-groups.sh
```

By default, the uninstaller:

* Stops and disables pull-ipa-local-groups.timer and pull-ipa-local-groups.service.
* Removes /etc/systemd/system/pull-ipa-local-groups.timer, /etc/systemd/system/pull-ipa-local-groups.service, and /usr/local/bin/pull-ipa-local-groups.py.
* Reloads systemd and resets failed unit states.
* Preserves /etc/default/pull-ipa-local-groups (so your CONFIG_URL and credentials are not lost) as well as /etc/security/ipa-local-groups.conf.

To also remove /etc/default/pull-ipa-local-groups, pass --purge-config:

```sh
sudo ./setup/uninstall-pull-ipa-local-groups.sh --purge-config
```
