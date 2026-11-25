# sync-local-groups

Automatic synchronization of local Linux system groups (`/etc/group`) for centrally managed user accounts (e.g., **FreeIPA** or **SSSD**) upon login via PAM (`pam_exec.so`) or manual CLI execution.

## Overview & How It Works

When using centralized directory services such as FreeIPA, users logging into local workstations or servers often require membership in local system groups (e.g., `audio`, `video`, `kvm`, `libvirt`, `docker`, `plugdev`) to access local hardware or services.

`sync-local-groups.py` resolves this dynamically when a user session is opened (`PAM_TYPE=open_session`):

1. **Group Discovery:** Retrieves all effective groups for the user (FreeIPA + local via NSS) as well as current local group memberships from `/etc/group`.
2. **Rule Evaluation:** Evaluates the configuration file `/etc/security/ipa-local-groups.conf` from top to bottom (**"Last Match Wins"**).
3. **Synchronization:**
   * Missing local groups are added via `usermod -aG`.
   * Explicitly negated groups (`!group`) are removed via `gpasswd -d`.
   * Groups that do not exist on the local host are silently ignored.
4. **Logging:** All applied membership changes are logged to `syslog` (`LOG_INFO`).

## Repository Layout

| File | Target Path After Installation | Description |
| :--- | :--- | :--- |
| `bin/sync-local-groups.py` | `/usr/local/bin/sync-local-groups.py` | Main script for evaluating and applying group memberships |
| `config/ipa-local-groups.conf` | `/etc/security/ipa-local-groups.conf` | Rule set for user and group mappings |
| `setup/install-sync-local-groups.sh` | *(repository only)* | Installation script (installs files & configures PAM) |
| `setup/uninstall-sync-local-groups.sh` | *(repository only)* | Uninstallation script (removes PAM hook, script & optionally config) |
| `setup-user-cache.README.md` | *(repository only)* | Documentation for this tool

## Prerequisites

* Python 3 (`python3`)
* Standard user management utilities (`usermod`, `gpasswd`)
* PAM with `pam_exec.so`
* Root privileges (`sudo`) for installation and production execution

## Installation

1. Make the scripts executable and run the installer as `root`:

   ```sh
   chmod +x setup/install-sync-local-groups.sh setup/uninstall-sync-local-groups.sh
   sudo ./setup/install-sync-local-groups.sh
   ```

2. **Custom PAM Configuration (optional):**

   By default, `install-sync-local-groups.sh` adds the hook to `/etc/pam.d/common-session` (Debian/Ubuntu). On RHEL, Fedora, or SUSE systems, you can specify the target PAM file via `PAM_TARGET`:

   ```sh
   sudo PAM_TARGET=/etc/pam.d/system-auth ./setup/install-sync-local-groups.sh
   ```

   *(A timestamped backup of the PAM file is automatically created before any changes are made.)*

## Configuration (`/etc/security/ipa-local-groups.conf`)

### Syntax

```text
<user> <ipa-group> <local-groups>
```

* **`<user>`**: Username, comma-separated list (`alice,bob`), negation (`!guest`), or wildcard (`*`).
* **`<ipa-group>`**: Effective group (e.g., FreeIPA group), comma-separated list, negation (`!external`), or wildcard (`*`).
* **`<local-groups>`**: Comma-separated list of target local groups. Prefixing a group with `!` removes the user from that local group.
* **Precedence:** All matching lines are evaluated from top to bottom; later rules override earlier ones (**Last Match Wins**).
* Comments start with `#`.

### Example Configuration

```text
# Grant local desktop/hardware groups to all members of the FreeIPA "workstation-users" group
*       workstation-users   audio,video,render,input,kvm,libvirt,plugdev,dialout,uucp,netdev,bluetooth,lpadmin,scanner

# Grant user "alice" additional access to Docker and Wireshark
alice   *                   docker,wireshark

# Ensure user "guest" is never in kvm or docker (overrides earlier rules)
guest   *                   !kvm,!docker

# Grant members of the "admins" group read access to system logs
*       admins              adm,systemd-journal
```

## Usage & Testing (CLI)

In addition to automatic execution via PAM, the script can be invoked manually at any time—especially in **dry-run mode (`-n`)**, which previews changes without modifying the system:

```sh
# Perform a dry-run for a specific user
sudo /usr/local/bin/sync-local-groups.py -n -u alice

# Perform a dry-run using a custom configuration file
sudo /usr/local/bin/sync-local-groups.py -n -u alice -c ./ipa-local-groups.conf

# Apply changes manually for a user
sudo /usr/local/bin/sync-local-groups.py -u alice
```

### CLI Options

| Option | Description |
| :--- | :--- |
| `-n`, `--dry-run` | Show which groups would be added or removed without executing `usermod` or `gpasswd` |
| `-u`, `--user <USER>` | Target username for manual execution (defaults to `PAM_USER` if omitted) |
| `-c`, `--config <PATH>` | Path to configuration file (default: `/etc/security/ipa-local-groups.conf`) |

## Logging & Troubleshooting

* **Check Syslog:** All group changes are logged with the `sync-local-groups:` prefix:

  ```sh
  journalctl -t python3 | grep sync-local-groups
  # or via traditional log files:
  grep sync-local-groups /var/log/syslog /var/log/auth.log
  ```

* **PAM Ordering Note:** Because `pam_exec` runs during login, newly added group memberships take effect immediately for the newly started session (provided `pam_exec` is placed before session-initializing modules or the login shell initializes credentials afterward). If needed, move `session optional pam_exec.so /usr/local/bin/sync-local-groups.py` higher up in your PAM configuration file.

## Uninstallation

By default, `uninstall-sync-local-groups.sh` removes the PAM entry and `/usr/local/bin/sync-local-groups.py`, while preserving `/etc/security/ipa-local-groups.conf`:

```sh
sudo ./setup/uninstall-sync-local-groups.sh
```

To remove the configuration file as well:

```sh
sudo ./setup/uninstall-sync-local-groups.sh --purge-config
```

If a custom `PAM_TARGET` was used during installation, pass the same variable when uninstalling:

```sh
sudo PAM_TARGET=/etc/pam.d/system-auth ./setup/uninstall-sync-local-groups.sh
```
