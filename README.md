# FreeIPA Client & Storage Automation Tools

A lightweight collection of Python and shell utilities to automate home directory provisioning, NFS cache offloading, and local Linux group synchronization in environments managed by **FreeIPA** (or SSSD).

## Architecture Overview

These four modular components work together across your NAS/storage server and Linux client workstations:

* **Storage / NAS (`zfs-ipa-homedir`):** Provisions, audits, and monitors ZFS home datasets based on FreeIPA user accounts.
* **Client Configuration (`pull-ipa-local-groups`):** Periodically fetches and validates central group-mapping rules from an HTTPS endpoint.
* **Client Login — Groups (`sync-local-groups`):** Evaluates those rules via PAM during login to dynamically assign or revoke local system groups (e.g., `docker`, `kvm`, `audio`).
* **Client Login — Performance (`setup-user-cache`):** Redirects `$HOME/.cache` from NFS shares to a local filesystem (`XDG_CACHE_HOME`) to prevent I/O bottlenecks and `.nfs*` lock issues.

---

## Components

| Component | Target Host | Description | Documentation |
| :--- | :--- | :--- | :--- |
| **`zfs-ipa-homedir`** | Storage / NAS | Creates, destroys, reconciles, and monitors ZFS home datasets (`<pool>/<user>`) against FreeIPA user accounts, including quota and permission checks with Icinga2/Nagios & JSON output. | [zfs-ipa-homedir.README.md](./zfs-ipa-homedir.README.md) |
| **`pull-ipa-local-groups`** | Linux Clients | Systemd timer/service that securely fetches, validates, and atomically updates `/etc/security/ipa-local-groups.conf` over HTTPS using only the Python 3 standard library. | [pull-ipa-local-groups.README.md](./pull-ipa-local-groups.README.md) |
| **`sync-local-groups`** | Linux Clients | PAM `pam_exec` hook that dynamically updates local `/etc/group` memberships upon session start based on FreeIPA groups and `/etc/security/ipa-local-groups.conf`. | [sync-local-groups.README.md](./sync-local-groups.README.md) |
| **`setup-user-cache`** | Linux Clients | Login profile script (`/etc/profile.d`) that symlinks `$HOME/.cache` on NFS-mounted home directories to a local directory and exports `XDG_CACHE_HOME`. | [setup-user-cache-README.md](./setup-user-cache-README.md) |

---

## Quick Start

Each tool is self-contained with its own installer, uninstaller, and configuration defaults. Refer to the individual READMEs linked above for detailed setup instructions, CLI flags, and configuration examples.

1. **On your ZFS/NFS Server:** Configure `zfs-ipa-homedir.py` via cron or Icinga2/Nagios to automate user home dataset creation and monitoring.
2. **On your Linux Clients:**
   * Install `sync-local-groups` to enable PAM-based local group mapping.
   * Install `pull-ipa-local-groups` if you want clients to pull `/etc/security/ipa-local-groups.conf` automatically from a central web server.
   * Install `setup-user-cache` if client home directories are mounted over NFS.
