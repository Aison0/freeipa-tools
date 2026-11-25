# zfs-ipa-homedir.py

Manage ZFS home datasets (`<pool>/<user>`) and mount directories (`<mount-base>/<user>`) on a NAS integrated with FreeIPA.

## Features

- **Provisioning & Teardown (`create`, `destroy`):** Create ZFS datasets with quotas, populate from `/etc/skel`, set ownership (`UID:GID`) and mode (`0700` by default), or recursively destroy datasets.
- **Reconciliation & Auditing (`check`):** Compare FreeIPA users (`ipa user-find --all --raw --sizelimit=0`) against existing ZFS datasets under `<pool>`.
- **Account & Path Conflict Detection:** Distinguishes active vs. locked accounts (`nsaccountlock: TRUE`), mismatched home directory names (`userdir != uid`), missing `homedirectory` attributes, and paths outside `<home-prefix>/<userdir>`.
- **Permissions & Quota Checks:** Verify or repair directory ownership and octal mode (`--check-perms`, `--fix-perms`) and evaluate quota usage against warning/critical thresholds (`--warn-pct`, `--crit-pct`).
- **Multiple Output Modes:** Human-readable console text, Icinga2/Nagios plugin output with performance data (`--icinga` / `--nagios`), or structured JSON (`--json`).

## Requirements

- Python 3.10+ (standard library only)
- OpenZFS CLI (`zfs`)
- FreeIPA client CLI (`ipa`) with valid authentication/ticket where required

## CLI Usage

### Global Options

```text
--pool POOL             ZFS base dataset (default: tank/home)
--mount-base PATH       Mount base directory (default: /srv/nfs/home)
--skel PATH             Skeleton directory (default: /etc/skel)
--home-prefix PREFIX    Expected IPA home base path (default: /home/remote)
--mode MODE             Expected directory mode in octal (default: 0700)
--dry-run               Show changes without executing them
```

### `create`

Create a ZFS home dataset and directory for a user:

```bash
./zfs-ipa-homedir.py create jdoe --quota 50G
```

Skip checking the FreeIPA `homedirectory` attribute:

```bash
./zfs-ipa-homedir.py create jdoe --quota 100G --ignore-homedirectory
```

### `destroy`

Destroy a user's ZFS home dataset recursively (prompts for confirmation unless `-f` / `--force` is specified):

```bash
./zfs-ipa-homedir.py destroy jdoe
./zfs-ipa-homedir.py destroy jdoe --force
```

### `check`

Check a single user:

```bash
./zfs-ipa-homedir.py check jdoe
./zfs-ipa-homedir.py check jdoe --fix-perms --dry-run
```

Reconcile all users and datasets:

```bash
./zfs-ipa-homedir.py check
./zfs-ipa-homedir.py check --create --check-perms --dry-run
./zfs-ipa-homedir.py check --destroy --force --min-uid 1000
```

> Note: `--json` and `--icinga` / `--nagios` are mutually exclusive.

## Status Categories

| Category | Meaning | Auto-Create (`--create`) | Auto-Destroy (`--destroy`) |
| :--- | :--- | :--- | :--- |
| `[OK]` | Active user with valid `<home-prefix>/<uid>` and existing dataset | No | No |
| `[MISSING]` | Active user with valid `<home-prefix>/<uid>`, dataset missing | Yes | No |
| `[LOCKED]` | Account locked (`nsaccountlock: TRUE`) | No | No |
| `[MISMATCH]` | `<userdir>` from `homedirectory` differs from `uid` | No | No |
| `[NO_HOME]` | No `homedirectory` attribute set | No | No |
| `[BAD_PATH]` | `homedirectory` is not directly under `<home-prefix>/<userdir>` | No | No |
| `[ORPHAN]` | Dataset under `<pool>` with no matching `uid` or `userdir` | No | Yes |
| `[PERM_WARN]` | Mismatched `UID`, `GID`, or octal mode on `<mount-base>/<user>` | No | No |
| `[QUOTA_WARN]` | Storage usage >= `--warn-pct` (default `80%`) of quota | No | No |
| `[QUOTA_CRIT]` | Storage usage >= `--crit-pct` (default `95%`) of quota | No | No |

## Monitoring (`--icinga` / `--nagios`)

Exit codes follow Nagios/Icinga conventions:

- `0` (`OK`): No relevant discrepancies and usage below `--warn-pct`
- `1` (`WARNING`): Missing homes, conflicts, invalid paths, or usage >= `--warn-pct`
- `2` (`CRITICAL`): Orphaned datasets, permission issues, or usage >= `--crit-pct`
- `3` (`UNKNOWN`): Command, ZFS, or FreeIPA errors

### Examples

```bash
./zfs-ipa-homedir.py check jdoe --icinga
```

```text
OK - jdoe: OK | Storage: 12.4 GiB / 50.0 GiB (24.8%) [OK] | dataset=1 perm_issues=0 used_bytes=13314398617B quota_bytes=53687091200B usage_pct=24.80%;80.0;95.0
```

```bash
./zfs-ipa-homedir.py check --check-perms --icinga
```

```text
WARNING - ZFS Homes (tank/home): ok=42, missing=1, locked=3, mismatch=0, no_home=0, bad_path=0, orphan=0, quota_warn=1, quota_crit=0, perm_warn=0 (WARN storage: alice (84.2%)) | ok=42 missing=1 locked=3 mismatch=0 no_home=0 bad_path=0 orphan=0 quota_warn=1 quota_crit=0 perm_warn=0
```

## Machine-Readable Output (`--json`)

`--json` prints structured JSON to `stdout` (and a JSON `{"error": "..."}` object on `CommandError`).

### Single-User Example

```bash
./zfs-ipa-homedir.py check jdoe --json
```

```json
{
  "command": "check",
  "scope": "single",
  "pool": "tank/home",
  "user": {
    "uid": "jdoe",
    "home_directory": "/home/remote/jdoe",
    "userdir": "jdoe",
    "locked": false
  },
  "expected_home_directory": "/home/remote/jdoe",
  "dataset": "tank/home/jdoe",
  "dataset_exists": true,
  "directory": "/srv/nfs/home/jdoe",
  "directory_exists": true,
  "action": "OK",
  "action_detail": "OK (Dataset present)",
  "thresholds": {
    "warn_pct": 80.0,
    "crit_pct": 95.0
  },
  "storage": {
    "dataset": "tank/home/jdoe",
    "used_bytes": 13314398617,
    "quota_bytes": 53687091200,
    "usage_pct": 24.8,
    "status": "OK"
  },
  "permissions": {
    "path": "/srv/nfs/home/jdoe",
    "ok": true,
    "actual_uid": 1001,
    "expected_uid": 1001,
    "actual_gid": 1001,
    "expected_gid": 1001,
    "actual_mode": "0o700",
    "expected_mode": "0o700",
    "issues": [],
    "fix_message": null
  }
}
```

### All-Users Example

```bash
./zfs-ipa-homedir.py check --check-perms --json
```

```json
{
  "command": "check",
  "scope": "all",
  "pool": "tank/home",
  "mount_base": "/srv/nfs/home",
  "home_prefix": "/home/remote",
  "dry_run": false,
  "thresholds": {
    "warn_pct": 80.0,
    "crit_pct": 95.0
  },
  "summary": {
    "ok": 1,
    "missing": 0,
    "locked_with_home": 0,
    "locked_without_home": 0,
    "mismatch": 0,
    "no_home": 0,
    "bad_path": 0,
    "orphan": 0,
    "quota_warn": 0,
    "quota_crit": 0,
    "perm_warn": 0
  },
  "classification": {
    "active_valid": {
      "jdoe": {
        "uid": "jdoe",
        "home_directory": "/home/remote/jdoe",
        "userdir": "jdoe",
        "locked": false
      }
    },
    "locked_valid": {},
    "missing_home_attr": {},
    "invalid_home_path": {},
    "mismatched_home": {}
  },
  "present_homes": {
    "jdoe": {
      "dataset": "tank/home/jdoe",
      "used_bytes": 13314398617,
      "quota_bytes": 53687091200,
      "usage_pct": 24.8,
      "status": "OK",
      "permissions": {
        "path": "/srv/nfs/home/jdoe",
        "ok": true,
        "actual_uid": 1001,
        "expected_uid": 1001,
        "actual_gid": 1001,
        "expected_gid": 1001,
        "actual_mode": "0o700",
        "expected_mode": "0o700",
        "issues": [],
        "fix_message": null
      }
    }
  },
  "missing_homes": [],
  "locked_with_home": {},
  "locked_without_home": [],
  "mismatched_home": {},
  "orphaned_homes": {},
  "quota_warn": [],
  "quota_crit": [],
  "permission_warnings": [],
  "permission_actions": [],
  "created_homes": [],
  "destroyed_homes": []
}
```
