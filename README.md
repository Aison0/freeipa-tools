# freeipa-tools
Helper scripts and PAM/profile configurations for integrating Linux clients (**Ubuntu** / **Gentoo**) with **FreeIPA**, featuring per-user **ZFS NFSv4 (`sec=krb5p`)** home directories, local XDG cache redirection, and dynamic local hardware group synchronization.

---

## Architecture Overview

* **Identity & Policy (FreeIPA):** Centralized authentication, automount maps (`auto.master`, `auto.home`, `auto.direct`), `automember` rules, Sudo policies, and HBAC access control.
* **Storage Backend (Dedicated ZFS NFS Server):**
  * Each FreeIPA user gets a dedicated child ZFS dataset (`tank/home/<user>`) with individual quotas and inherited `sharenfs` properties.
  * Exported via **Kerberos-encrypted NFSv4 (`sec=krb5p`)**.
  * Mounted locally on the NFS server via recursive bind mount (`rbind`) to `/home/remote`.
* **Linux Clients (Ubuntu & Gentoo):**
  * Remote home directories are mounted on-demand via `autofs` under `/home/remote/<user>` (keeping `/home/<user>` clean for local fallback accounts).
  * `~/.cache` and `XDG_CACHE_HOME` are redirected to fast local storage (`/var/cache/users/<user>`, mode `1777`) to avoid small-file I/O overhead over NFS.
  * Local hardware/system groups (`video`, `render`, `kvm`, `audio`, `plugdev`, etc.) are dynamically synchronized per host upon login via `pam_exec` and `sync-local-groups.py`.

---

## Repository Structure

```text
.
├── bin/
│   ├── create-ipa-homedir.sh       # Provisions per-user ZFS datasets on the NFS server
│   └── sync-local-groups.py        # Synchronizes local /etc/group memberships via PAM or CLI
├── config/
│   └── ipa-local-groups.conf       # Rule definitions for sync-local-groups.py
├── pam-configs/
│   └── sync-local-groups           # Update-safe Debian/Ubuntu pam-auth-update profile
├── profile.d/
│   └── local-cache.sh              # Redirects XDG_CACHE_HOME & ~/.cache to local disk
└── README.md
```

---

## 1. NFS Server Setup (ZFS + NFSv4 `sec=krb5p`)

### Create Parent ZFS Dataset
```bash
sudo zfs create -o mountpoint=/srv/nfs/home tank/home
sudo zfs set sharenfs="sec=krb5p,rw=@172.20.1.0/24,no_subtree_check,root_squash" tank/home
sudo systemctl enable --now nfs-kernel-server rpc-gssd zfs-share
```

### Recursive Bind Mount for Local Access on the NFS Server
Add the following line to `/etc/fstab` so `/home/remote` exposes all user ZFS child datasets locally without using `autofs` on the server itself:
```text
/srv/nfs/home   /home/remote   none   rbind,x-systemd.requires=zfs-mount.service   0   0
```

### Provision a User Home Dataset
Install `bin/create-ipa-homedir.sh` to `/usr/local/sbin/create-ipa-homedir.sh` (`chmod 755`) and run:
```bash
sudo create-ipa-homedir.sh <username> 50G
```

---

## 2. FreeIPA Server Configuration

### Set Default Home Directory & Automount Maps
```bash
kinit admin

# Set global default home base path
ipa config-mod --homedirectory=/home/remote

# Configure automount maps for /home/remote
ipa automountmap-add default auto.home
ipa automountkey-add default auto.home --key="*" --info="-fstype=nfs4,rw,sec=krb5p nfs.example.local:/srv/nfs/home/&"
ipa automountkey-add default auto.master --key="/home/remote" --info="auto.home"
```

### Configure `workstation-users` Group & Automember Rule
```bash
ipa group-add workstation-users --desc="Users granted local workstation hardware groups"
ipa automember-add workstation-users --type=group
ipa automember-add-condition workstation-users --type=group --key=homedirectory --inclusive-regex="^/home/remote/.*"
```

---

## 3. Client Setup (Ubuntu / Gentoo)

### Join FreeIPA & Enable Automount
```bash
sudo apt install -y freeipa-client nfs-common autofs
sudo ipa-client-install --no-ntp --domain=example.local --server=ipa.example.local --realm=EXAMPLE.LOCAL
sudo mkdir -p /home/remote
sudo ipa-client-automount
sudo systemctl enable --now rpc-gssd autofs
```
*(Ensure `Domain = example.local` matches across `/etc/idmapd.conf` on both the NFS server and all clients.)*

### Configure Local Cache Redirection (`local-cache.sh`)
Create the sticky-bit local cache directory and install the profile script:
```bash
sudo mkdir -p /var/cache/users
sudo chmod 1777 /var/cache/users
sudo cp profile.d/local-cache.sh /etc/profile.d/local-cache.sh
```

### Install Dynamic Local Group Synchronization (`sync-local-groups.py`)

1. **Copy the script and rule configuration:**
   ```bash
   sudo mkdir -p /usr/share/scripts/ipa
   sudo cp bin/sync-local-groups.py /usr/share/scripts/ipa/sync-local-groups.py
   sudo chmod 755 /usr/share/scripts/ipa/sync-local-groups.py
   sudo cp config/ipa-local-groups.conf /etc/security/ipa-local-groups.conf
   ```

2. **Enable in PAM:**
   * **Ubuntu / Debian (update-safe via `pam-auth-update`):**
     ```bash
     sudo cp pam-configs/sync-local-groups /usr/share/pam-configs/sync-local-groups
     sudo pam-auth-update --package
     ```
   * **Gentoo (`/etc/pam.d/system-auth`):**
     Add the following line to the `session` stack:
     ```text
     session optional pam_exec.so /usr/share/scripts/ipa/sync-local-groups.py
     ```

---

## Usage & Testing (`sync-local-groups.py`)

### Configuration Format (`/etc/security/ipa-local-groups.conf`)
```text
# Format: <user> <ipa-group> <local-groups>
# Evaluated top-to-bottom ("Last Match Wins"). Supports *, lists (a,b), and negation (!x).

*       workstation-users   adm,systemd-journal,audio,video,render,input,kvm,libvirt,plugdev,dialout,uucp,netdev,bluetooth,lpadmin,scanner
# alice *                   docker,wireshark
# guest *                   !kvm,!docker
```

### Dry-Run & Manual Execution
```bash
# Preview changes without modifying /etc/group
sudo /usr/share/scripts/ipa/sync-local-groups.py -n -u <username>

# Apply rules immediately
sudo /usr/share/scripts/ipa/sync-local-groups.py -u <username>
```

---

## License

MIT License

