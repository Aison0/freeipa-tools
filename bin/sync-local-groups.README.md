# sync-local-groups.py

A lightweight Python script that dynamically synchronizes local Linux system groups (`/etc/group`) for centrally managed users (such as **FreeIPA**, LDAP, or Active Directory) upon login via **PAM (`pam_exec`)** or manual CLI execution.

## Why Use This?

In mixed Linux environments (e.g., **Ubuntu** and **Gentoo**), local hardware and system groups (`video`, `render`, `kvm`, `audio`, `plugdev`, `dialout`, `uucp`, etc.) have different numeric GIDs on each host. Furthermore, `pam_group.so` often fails to apply supplementary groups reliably during OpenSSH (`sshd`) sessions using SSH keys or Kerberos/GSSAPI tickets.

`sync-local-groups.py` solves this cleanly by:
* Mapping FreeIPA/LDAP groups or specific usernames to local host groups via a simple rule file.
* Resolving group names strictly against the local `/etc/group` of the target machine (automatically ignoring groups that do not exist on a given host).
* Persisting memberships cleanly via `usermod -aG` and `gpasswd -d`, so they apply consistently across SSH, graphical logins, `sudo`, and background services.

---

## Features

* **Zero external dependencies:** Uses only the Python 3 standard library (`argparse`, `grp`, `os`, `pwd`, `shutil`, `subprocess`, `syslog`).
* **Flexible rule syntax:** Supports wildcards (`*`), comma-separated lists (`a,b`), and negations (`!group`) across all columns.
* **Layered evaluation ("Last Match Wins"):** Evaluates all matching rules top-to-bottom, allowing base rules to be overridden per user or group.
* **PAM & CLI dual mode:** Runs silently as a PAM `open_session` hook or interactively from the command line.
* **Dry-run mode (`-n`):** Preview exact additions and removals before touching `/etc/group`.
* **Syslog auditing:** Logs all applied group changes to `syslog` (`LOG_INFO`).

---

## Installation

### 1. Install the Script
```bash
sudo mkdir -p /usr/share/scripts/ipa
sudo cp sync-local-groups.py /usr/share/scripts/ipa/sync-local-groups.py
sudo chown root:root /usr/share/scripts/ipa/sync-local-groups.py
sudo chmod 755 /usr/share/scripts/ipa/sync-local-groups.py
```

### 2. Create the Configuration File
Create `/etc/security/ipa-local-groups.conf`:
```bash
sudo cp ipa-local-groups.conf /etc/security/ipa-local-groups.conf
sudo chown root:root /etc/security/ipa-local-groups.conf
sudo chmod 644 /etc/security/ipa-local-groups.conf
```

### 3. Enable in PAM

#### Ubuntu / Debian (Update-Safe via `pam-auth-update`)
Create `/usr/share/pam-configs/sync-local-groups` (note: **tabs** are required before `optional` and `pam_exec.so`):
```text
Name: Sync local groups for FreeIPA users
Default: yes
Priority: 100
Session-Type: Additional
Session:
	optional			pam_exec.so /usr/share/scripts/ipa/sync-local-groups.py
```
Activate the PAM profile:
```bash
sudo pam-auth-update --package
```

#### Gentoo (`/etc/pam.d/system-auth`)
Add the following line to the `session` stack in `/etc/pam.d/system-auth`:
```text
session optional pam_exec.so /usr/share/scripts/ipa/sync-local-groups.py
```

---

## Configuration Syntax

Each line in `/etc/security/ipa-local-groups.conf` consists of three whitespace-separated columns:

```text
<user>   <ipa-group>   <local-groups>
```

* **`<user>`:** Exact username, comma-separated list (`alice,bob`), negated user (`!guest`), or `*` for all users.
* **`<ipa-group>`:** FreeIPA/NSS group name, comma-separated list, negated group (`!service-accounts`), or `*`.
* **`<local-groups>`:** Comma-separated list of local `/etc/group` names to add (`group`) or explicitly remove (`!group`).

### Example `/etc/security/ipa-local-groups.conf`
```text
# Format: <user> <ipa-group> <local-groups>
# Rules are evaluated top-to-bottom (later matching rules override earlier ones).

# 1. Grant standard workstation hardware groups to all members of 'workstation-users'
*       workstation-users   adm,systemd-journal,audio,video,render,input,kvm,libvirt,plugdev,dialout,uucp,netdev,bluetooth,lpadmin,scanner

# 2. Grant additional local groups to specific administrators/developers
alice   *                   docker,wireshark

# 3. Override base rule: explicitly remove 'kvm' and 'docker' from 'guest'
guest   *                   !kvm,!docker
```

---

## CLI Usage & Testing

```text
usage: sync-local-groups.py [-h] [-n] [-c CONFIG] [-u USER]

Synchronize local Linux groups based on /etc/security/ipa-local-groups.conf

options:
  -h, --help            show this help message and exit
  -n, --dry-run         Show which groups would be added or removed without making changes
  -c CONFIG, --config CONFIG
                        Path to configuration file (default: /etc/security/ipa-local-groups.conf)
  -u USER, --user USER  Target username for manual CLI execution (defaults to PAM_USER if omitted)
```

### Examples
```bash
# Preview changes for a user without modifying /etc/group
sudo /usr/share/scripts/ipa/sync-local-groups.py -n -u alice

# Apply group rules immediately for a user
sudo /usr/share/scripts/ipa/sync-local-groups.py -u alice

# Test with an alternative config file
sudo /usr/share/scripts/ipa/sync-local-groups.py -n -c ./test-groups.conf -u alice
```

---

## Security Notes

* Because `pam_exec.so` runs the script as `root` during session creation, ensure that both `/usr/share/scripts/ipa/sync-local-groups.py` and `/etc/security/ipa-local-groups.conf` are owned by `root:root` and not writable by unprivileged users (`0755` / `0644`).
* Avoid granting `sudo` or `wheel` via local group sync; manage privilege escalation centrally via **FreeIPA Sudo rules** instead.

---

## License

MIT License
