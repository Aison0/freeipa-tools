#!/usr/bin/env python3
"""
Technical Overview: zfs-ipa-home.py
===================================

Purpose:
  Manages ZFS home datasets (<pool>/<user>) and their mount directories
  (<mount-base>/<user>) on a NAS integrated with FreeIPA.

Commands & Behavior:
  1. create <user> [--quota 50G] [--ignore-homedirectory]
     - Checks by default whether the user in FreeIPA (`ipa user-show <user> --all --raw`)
       has a `homedirectory` attribute under `<home-prefix>/<user>` (default:
       `/home/remote/<user>`). Use `--ignore-homedirectory` to skip this check.
     - Creates the ZFS dataset `<pool>/<user>` with the specified quota, copies
       `/etc/skel` into `<mount-base>/<user>`, and sets owner (`UID:GID`) and
       mode (`700` or `--mode`).

  2. destroy <user> [-f|--force]
     - Recursively destroys the ZFS dataset `<pool>/<user>` (`zfs destroy -r`)
       if it exists (prompts for confirmation unless `--force` is given).

  3. check [user] [--create] [--destroy] [--check-perms] [--fix-perms]
           [--warn-pct 80] [--crit-pct 95] [--icinga|--nagios | --json]
     - With <user>: Checks FreeIPA account status, `homedirectory`, ZFS dataset,
       mount directory, ownership/permissions (`UID:GID`, octal mode), and
       storage usage/quota.
     - Without <user>: Reconciles all FreeIPA users (`ipa user-find --all --raw --sizelimit=0`)
       against existing datasets under `<pool>`. Optionally creates missing
       homes (`--create`), removes orphaned datasets (`--destroy`), checks
       permissions (`--check-perms`), or fixes permission mismatches (`--fix-perms`).

Status Categories & Conflict Handling:
  - [OK]         Active user with valid `homedirectory` under `<home-prefix>/<uid>`
                 and an existing dataset.
  - [MISSING]    Active user with valid `homedirectory` under `<home-prefix>/<uid>`,
                 but missing dataset (created by `check --create`).
  - [LOCKED]     FreeIPA account with `nsaccountlock: TRUE`. Skipped during `--create`
                 and protected from deletion as an orphan during `--destroy`.
  - [MISMATCH]   `homedirectory` is under `<home-prefix>/<userdir>`, but `<userdir>`
                 does not match `uid` (e.g., after a rename). Reported as a conflict
                 and neither auto-created nor auto-destroyed.
  - [NO_HOME]    User without a `homedirectory` attribute set (no auto-create).
  - [BAD_PATH]   `homedirectory` is not directly under `<home-prefix>/<userdir>`
                 (no auto-create).
  - [ORPHAN]     Dataset under `<pool>` with no matching user (`uid` or `userdir`)
                 (candidate for `check --destroy`).
  - [PERM_WARN]  Mismatched `UID`, `GID`, or octal mode on `<mount-base>/<user>`
                 (checked in `check [user]` and in `check --check-perms` / `--fix-perms`).
  - [QUOTA_WARN] / [QUOTA_CRIT]
                 Storage usage (`used` relative to `quota`) reaches or exceeds
                 `--warn-pct` (default 80%) or `--crit-pct` (default 95%).

Icinga2 / Nagios Mode (--icinga / --nagios) & JSON Mode (--json):
  - `--json` and `--icinga`/`--nagios` are mutually exclusive.
  - `--json` outputs structured JSON to stdout (or a JSON error object on failure).
  - Exit codes: 0 = OK, 1 = WARNING, 2 = CRITICAL, 3 = UNKNOWN.
  - `check <user> --icinga`:
      CRITICAL on `DESTROY_CANDIDATE`, unresolved permission/owner issues, or
      usage >= `--crit-pct`.
      WARNING on `CREATE_NEEDED`, `CONFLICT_MANUAL`, `NO_HOME_ATTR`, `BAD_HOME_PATH`,
      or usage >= `--warn-pct`.
      OK otherwise (including `LOCKED` without conflicts and below thresholds).
  - `check --icinga` (all users):
      CRITICAL on `ORPHAN`, unresolved `PERM_WARN`, or usage >= `--crit-pct`.
      WARNING on `MISSING`, `MISMATCH`, `NO_HOME`, `BAD_PATH`, or
      usage >= `--warn-pct`.
      OK otherwise. Includes performance data after the pipe (`| ...`).
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

POOL_PATH = "tank/home"
MOUNT_BASE = Path("/srv/nfs/home")
SKEL_DIR = Path("/etc/skel")
HOME_PREFIX = "/home/remote"
DEFAULT_QUOTA = "50G"
DEFAULT_MODE = 0o700
DEFAULT_WARN_PCT = 80.0
DEFAULT_CRIT_PCT = 95.0

EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2
EXIT_UNKNOWN = 3

TECHNICAL_EPILOG = """
Status Categories:
  [OK]          Active user with valid /home/remote/<uid> and existing dataset
  [MISSING]     Active user with valid /home/remote/<uid>, dataset missing (--create)
  [LOCKED]      Account locked (nsaccountlock: TRUE): no --create, no --destroy
  [MISMATCH]    Conflict: <userdir> from homedirectory != uid (no auto-create/destroy)
  [NO_HOME]     No homedirectory attribute set (no auto-create)
  [BAD_PATH]    homedirectory outside <home-prefix>/<userdir> (no auto-create)
  [ORPHAN]      Orphaned dataset with no matching user/userdir (--destroy)
  [PERM_WARN]   Mismatched UID, GID, or permissions on <mount-base>/<user>
  [QUOTA_WARN]  Storage usage >= --warn-pct (default 80%) of dataset quota
  [QUOTA_CRIT]  Storage usage >= --crit-pct (default 95%) of dataset quota

Output Formats (--json mutually exclusive with --icinga / --nagios):
  --json            Structured JSON output on stdout
  --icinga/--nagios Icinga2/Nagios plugin output + perfdata + exit codes 0..3

Icinga2 / Nagios (--icinga / --nagios):
  Exit 0 (OK)       No relevant discrepancies and below quota thresholds
  Exit 1 (WARNING)  Missing homes, conflicts, invalid paths, or usage >= --warn-pct
  Exit 2 (CRITICAL) Orphaned datasets, permission errors, or usage >= --crit-pct
  Exit 3 (UNKNOWN)  Command, ZFS, or FreeIPA errors
"""


class CommandError(RuntimeError):
    """Raised when a system, ZFS, or FreeIPA command fails."""


def extract_remote_userdir(home_directory: str | None, home_prefix: str) -> str | None:
    """Checks whether home_directory lies directly under <home_prefix>/<userdir> and returns <userdir>."""
    if not home_directory:
        return None
    prefix_path = PurePosixPath(home_prefix)
    home_path = PurePosixPath(home_directory.strip())
    if home_path.parent != prefix_path or not home_path.name:
        return None
    return home_path.name


@dataclass
class IpaUserRecord:
    uid: str
    home_directory: str | None
    locked: bool

    def remote_userdir(self, home_prefix: str = HOME_PREFIX) -> str | None:
        return extract_remote_userdir(self.home_directory, home_prefix)

    def to_dict(self, home_prefix: str = HOME_PREFIX) -> dict[str, object]:
        return {
            "uid": self.uid,
            "home_directory": self.home_directory,
            "userdir": self.remote_userdir(home_prefix),
            "locked": self.locked,
        }


@dataclass
class ClassifiedUsers:
    active_valid: dict[str, IpaUserRecord]
    locked_valid: dict[str, IpaUserRecord]
    missing_home_attr: dict[str, IpaUserRecord]
    invalid_home_path: dict[str, IpaUserRecord]
    mismatched_home: dict[str, IpaUserRecord]

    def to_dict(self, home_prefix: str = HOME_PREFIX) -> dict[str, object]:
        def map_records(mapping: dict[str, IpaUserRecord]) -> dict[str, dict[str, object]]:
            return {
                uid: rec.to_dict(home_prefix)
                for uid, rec in sorted(mapping.items())
            }

        return {
            "active_valid": map_records(self.active_valid),
            "locked_valid": map_records(self.locked_valid),
            "missing_home_attr": map_records(self.missing_home_attr),
            "invalid_home_path": map_records(self.invalid_home_path),
            "mismatched_home": map_records(self.mismatched_home),
        }


@dataclass
class PermissionCheckResult:
    ok: bool
    path: Path
    actual_uid: int | None = None
    expected_uid: int | None = None
    actual_gid: int | None = None
    expected_gid: int | None = None
    actual_mode: int | None = None
    expected_mode: int = DEFAULT_MODE
    issues: list[str] | None = None

    def to_dict(self, fix_message: str | None = None) -> dict[str, object]:
        return {
            "path": str(self.path),
            "ok": self.ok,
            "actual_uid": self.actual_uid,
            "expected_uid": self.expected_uid,
            "actual_gid": self.actual_gid,
            "expected_gid": self.expected_gid,
            "actual_mode": oct(self.actual_mode) if self.actual_mode is not None else None,
            "expected_mode": oct(self.expected_mode),
            "issues": list(self.issues or []),
            "fix_message": fix_message,
        }


@dataclass
class StorageUsageInfo:
    dataset: str
    used_bytes: int
    quota_bytes: int | None
    usage_pct: float | None
    status: str  # "OK", "WARN", "CRIT", "NO_QUOTA"

    def to_dict(self) -> dict[str, object]:
        return {
            "dataset": self.dataset,
            "used_bytes": self.used_bytes,
            "quota_bytes": self.quota_bytes,
            "usage_pct": round(self.usage_pct, 2) if self.usage_pct is not None else None,
            "status": self.status,
        }


@dataclass
class SingleCheckReport:
    username: str
    pool_path: str
    home_prefix: str
    expected_home_directory: str
    dataset: str
    dataset_exists: bool
    directory: Path
    directory_exists: bool
    user_record: IpaUserRecord | None
    action_code: str
    action_detail: str
    warn_pct: float
    crit_pct: float
    storage: StorageUsageInfo | None = None
    permissions: PermissionCheckResult | None = None
    permission_fix_message: str | None = None

    @property
    def user_exists(self) -> bool:
        return self.user_record is not None

    @property
    def is_locked(self) -> bool:
        return self.user_record.locked if self.user_record else False

    @property
    def home_directory(self) -> str | None:
        return self.user_record.home_directory if self.user_record else None

    @property
    def permission_issues(self) -> list[str]:
        if self.permissions is None or self.permissions.ok:
            return []
        return list(self.permissions.issues or [])

    def to_dict(self) -> dict[str, object]:
        user_dict = (
            self.user_record.to_dict(self.home_prefix)
            if self.user_record is not None
            else {"uid": self.username, "home_directory": None, "userdir": None, "locked": False}
        )
        return {
            "command": "check",
            "scope": "single",
            "pool": self.pool_path,
            "user": user_dict,
            "expected_home_directory": self.expected_home_directory,
            "dataset": self.dataset,
            "dataset_exists": self.dataset_exists,
            "directory": str(self.directory),
            "directory_exists": self.directory_exists,
            "action": self.action_code,
            "action_detail": self.action_detail,
            "thresholds": {"warn_pct": self.warn_pct, "crit_pct": self.crit_pct},
            "storage": self.storage.to_dict() if self.storage is not None else None,
            "permissions": (
                self.permissions.to_dict(fix_message=self.permission_fix_message)
                if self.permissions is not None
                else None
            ),
        }


@dataclass
class AllHomesReport:
    pool_path: str
    mount_base: Path
    home_prefix: str
    dry_run: bool
    warn_pct: float
    crit_pct: float
    check_perms_enabled: bool
    classified: ClassifiedUsers
    dataset_usages: dict[str, StorageUsageInfo]
    present_homes: list[str]
    missing_homes: list[str]
    locked_with_home: list[str]
    locked_without_home: list[str]
    orphaned_homes: list[str]
    quota_warn_users: list[str]
    quota_crit_users: list[str]
    permission_warnings: list[str] = field(default_factory=list)
    permission_actions: list[str] = field(default_factory=list)
    permission_results: dict[str, tuple[PermissionCheckResult, str | None]] = field(
        default_factory=dict
    )
    created_homes: list[str] = field(default_factory=list)
    destroyed_homes: list[str] = field(default_factory=list)

    @property
    def mismatch_count(self) -> int:
        return len(self.classified.mismatched_home)

    @property
    def no_home_count(self) -> int:
        return len(self.classified.missing_home_attr)

    @property
    def bad_path_count(self) -> int:
        return len(self.classified.invalid_home_path)

    @property
    def locked_total_count(self) -> int:
        return len(self.locked_with_home) + len(self.locked_without_home)

    def _home_entry_dict(self, user: str) -> dict[str, object]:
        entry: dict[str, object] = dict(self.dataset_usages[user].to_dict())
        if user in self.permission_results:
            p_res, p_msg = self.permission_results[user]
            entry["permissions"] = p_res.to_dict(fix_message=p_msg)
        return entry

    def to_dict(self) -> dict[str, object]:
        classified_dict = self.classified.to_dict(self.home_prefix)
        mismatched_json: dict[str, dict[str, object]] = {}
        for user in sorted(self.classified.mismatched_home.keys()):
            rec = self.classified.mismatched_home[user]
            udir = rec.remote_userdir(self.home_prefix)
            mismatched_json[user] = {
                "user": rec.to_dict(self.home_prefix),
                "dataset_uid": (
                    self.dataset_usages[user].dataset if user in self.dataset_usages else None
                ),
                "dataset_userdir": (
                    self.dataset_usages[udir].dataset
                    if udir is not None and udir in self.dataset_usages
                    else None
                ),
            }

        return {
            "command": "check",
            "scope": "all",
            "pool": self.pool_path,
            "mount_base": str(self.mount_base),
            "home_prefix": self.home_prefix,
            "dry_run": self.dry_run,
            "thresholds": {"warn_pct": self.warn_pct, "crit_pct": self.crit_pct},
            "summary": {
                "ok": len(self.present_homes),
                "missing": len(self.missing_homes),
                "locked_with_home": len(self.locked_with_home),
                "locked_without_home": len(self.locked_without_home),
                "mismatch": self.mismatch_count,
                "no_home": self.no_home_count,
                "bad_path": self.bad_path_count,
                "orphan": len(self.orphaned_homes),
                "quota_warn": len(self.quota_warn_users),
                "quota_crit": len(self.quota_crit_users),
                "perm_warn": len(self.permission_warnings),
            },
            "classification": classified_dict,
            "present_homes": {user: self._home_entry_dict(user) for user in self.present_homes},
            "missing_homes": list(self.missing_homes),
            "locked_with_home": {user: self._home_entry_dict(user) for user in self.locked_with_home},
            "locked_without_home": list(self.locked_without_home),
            "mismatched_home": mismatched_json,
            "orphaned_homes": {
                name: self.dataset_usages[name].to_dict() for name in self.orphaned_homes
            },
            "quota_warn": list(self.quota_warn_users),
            "quota_crit": list(self.quota_crit_users),
            "permission_warnings": list(self.permission_warnings),
            "permission_actions": list(self.permission_actions),
            "created_homes": list(self.created_homes),
            "destroyed_homes": list(self.destroyed_homes),
        }


class CheckReporter:
    """Base class for formatting output and handling errors for check runs."""

    def render_single(self, report: SingleCheckReport) -> int:
        raise NotImplementedError

    def render_all(self, report: AllHomesReport) -> int:
        raise NotImplementedError

    def render_error(self, exc: Exception) -> int:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


class TextReporter(CheckReporter):
    """Human-readable console output."""

    def render_single(self, report: SingleCheckReport) -> int:
        perm_summary = "n/a"
        if report.permissions is not None:
            p = report.permissions
            if p.ok:
                perm_summary = (
                    f"[OK] UID={p.actual_uid}, GID={p.actual_gid}, Mode={oct(p.actual_mode or 0)}"
                )
            else:
                issues_str = ", ".join(p.issues or [])
                if report.permission_fix_message:
                    perm_summary = f"[WARN] {issues_str} ({report.permission_fix_message})"
                else:
                    perm_summary = f"[WARN] {issues_str}"

        print(f"=== Check for user: {report.username} ===")
        print(
            f"User exists:          {'yes' if report.user_exists else 'no'}"
            + (" (locked)" if report.is_locked else "")
        )
        print(
            f"IPA homedirectory:  {report.home_directory or '-'}"
            + (f" (expected: {report.expected_home_directory})" if report.user_exists else "")
        )
        print(
            f"ZFS dataset:          {report.dataset} -> "
            f"{'present' if report.dataset_exists else 'missing'}"
        )
        print(
            f"Directory:            {report.directory} -> "
            f"{'present' if report.directory_exists else 'missing'}"
        )
        print(f"Storage usage:        {format_usage_summary(report.storage)}")
        print(f"Recommendation:       {report.action_detail}")
        if report.directory_exists:
            print(f"Permissions:          {perm_summary}")
            if report.permission_fix_message and report.permissions and report.permissions.ok:
                print(f"Permission fix:       {report.permission_fix_message}")
        return EXIT_OK

    def render_all(self, report: AllHomesReport) -> int:
        print("=== FreeIPA ZFS-Home Check ===")
        print(f"Present (OK):               {len(report.present_homes)}")
        print(f"Missing (no home):          {len(report.missing_homes)}")
        print(f"Locked (with home):         {len(report.locked_with_home)}")
        print(f"Locked (without home):      {len(report.locked_without_home)}")
        print(f"Conflict (userdir != uid):  {report.mismatch_count}")
        print(f"No homedirectory attr:      {report.no_home_count}")
        print(f"Invalid home path:          {report.bad_path_count}")
        print(f"Orphaned (no user):         {len(report.orphaned_homes)}")
        print(f"Storage warning (>= {report.warn_pct:g}%):  {len(report.quota_warn_users)}")
        print(f"Storage critical (>= {report.crit_pct:g}%): {len(report.quota_crit_users)}")
        if report.check_perms_enabled:
            print(f"Permission warnings:        {len(report.permission_warnings)}")
        print()

        if report.present_homes:
            print("Present home directories:")
            for user in report.present_homes:
                u_info = report.dataset_usages[user]
                line = f"  [OK]        {user} -> {u_info.dataset} | {format_usage_summary(u_info)}"
                if user in report.permission_results:
                    p_res, _ = report.permission_results[user]
                    if p_res.ok:
                        line += f" | Permissions OK ({oct(p_res.actual_mode or 0)})"
                    else:
                        line += f" | [PERM_WARN] {', '.join(p_res.issues or [])}"
                print(line)

        if report.missing_homes:
            print("Active users without home directory:")
            for user in report.missing_homes:
                print(f"  [MISSING]   {user} ({report.home_prefix.rstrip('/')}/{user})")

        if report.locked_with_home:
            print("Locked users with home directory (kept during --destroy):")
            for user in report.locked_with_home:
                u_info = report.dataset_usages[user]
                line = f"  [LOCKED]    {user} -> {u_info.dataset} | {format_usage_summary(u_info)}"
                if user in report.permission_results:
                    p_res, _ = report.permission_results[user]
                    if not p_res.ok:
                        line += f" | [PERM_WARN] {', '.join(p_res.issues or [])}"
                print(line)

        if report.locked_without_home:
            print("Locked users without home directory (skipped during --create):")
            for user in report.locked_without_home:
                print(f"  [LOCKED]    {user} (no dataset)")

        if report.classified.mismatched_home:
            print("Conflicts (homedirectory username does not match uid):")
            for user in sorted(report.classified.mismatched_home.keys()):
                rec = report.classified.mismatched_home[user]
                hdir = rec.home_directory or "-"
                udir = rec.remote_userdir(report.home_prefix) or "-"
                ds_uid = (
                    report.dataset_usages[user].dataset if user in report.dataset_usages else "-"
                )
                ds_udir = (
                    report.dataset_usages[udir].dataset
                    if udir in report.dataset_usages
                    else "-"
                )
                print(
                    f"  [MISMATCH]  uid={user} | homedirectory={hdir} (userdir={udir}) "
                    f"| ds(uid)={ds_uid} | ds(userdir)={ds_udir}"
                )

        if report.classified.missing_home_attr:
            print("Users without homedirectory attribute:")
            for user in sorted(report.classified.missing_home_attr.keys()):
                print(f"  [NO_HOME]   {user}")

        if report.classified.invalid_home_path:
            print(
                f"Users with homedirectory outside {report.home_prefix}/<userdir>:"
            )
            for user in sorted(report.classified.invalid_home_path.keys()):
                rec = report.classified.invalid_home_path[user]
                print(f"  [BAD_PATH]  {user} -> {rec.home_directory}")

        if report.orphaned_homes:
            print("Orphaned home directories:")
            for name in report.orphaned_homes:
                u_info = report.dataset_usages[name]
                print(f"  [ORPHAN]    {name} -> {u_info.dataset} | {format_usage_summary(u_info)}")

        if report.permission_actions:
            print("\n--- Permission Fixes ---")
            for action_msg in report.permission_actions:
                print(f"  {action_msg}")

        return EXIT_OK


class IcingaReporter(CheckReporter):
    """Icinga2 / Nagios plugin output with performance data and exit codes."""

    def render_single(self, report: SingleCheckReport) -> int:
        state = "OK"
        rc = EXIT_OK

        if report.action_code == "DESTROY_CANDIDATE":
            state, rc = "CRITICAL", EXIT_CRITICAL
        elif report.action_code in {
            "CONFLICT_MANUAL",
            "CREATE_NEEDED",
            "NO_HOME_ATTR",
            "BAD_HOME_PATH",
        }:
            state, rc = "WARNING", EXIT_WARNING

        if report.storage is not None:
            if report.storage.status == "CRIT":
                state, rc = "CRITICAL", EXIT_CRITICAL
            elif report.storage.status == "WARN" and rc < EXIT_WARNING:
                state, rc = "WARNING", EXIT_WARNING

        if report.permission_issues:
            state, rc = "CRITICAL", EXIT_CRITICAL

        ds_flag = 1 if report.dataset_exists else 0
        perm_flag = 1 if report.permission_issues else 0
        used_b = report.storage.used_bytes if report.storage else 0
        quota_b = (
            report.storage.quota_bytes
            if (report.storage and report.storage.quota_bytes is not None)
            else 0
        )
        pct_val = (
            f"{report.storage.usage_pct:.2f}"
            if (report.storage and report.storage.usage_pct is not None)
            else "0.00"
        )
        perfdata = (
            f"dataset={ds_flag} perm_issues={perm_flag} used_bytes={used_b}B "
            f"quota_bytes={quota_b}B usage_pct={pct_val}%;{report.warn_pct};{report.crit_pct}"
        )

        details: list[str] = [f"{report.username}: {report.action_detail}"]
        if report.storage is not None:
            details.append(f"Storage: {format_usage_summary(report.storage)}")
        if report.permission_issues:
            details.append(f"Permissions: {'; '.join(report.permission_issues)}")

        print(f"{state} - {' | '.join(details)} | {perfdata}")
        return rc

    def render_all(self, report: AllHomesReport) -> int:
        state = "OK"
        rc = EXIT_OK

        if report.orphaned_homes or report.permission_warnings or report.quota_crit_users:
            state, rc = "CRITICAL", EXIT_CRITICAL
        elif (
            report.missing_homes
            or report.mismatch_count
            or report.no_home_count
            or report.bad_path_count
            or report.quota_warn_users
        ):
            state, rc = "WARNING", EXIT_WARNING

        parts_msg = [
            f"ok={len(report.present_homes)}",
            f"missing={len(report.missing_homes)}",
            f"locked={report.locked_total_count}",
            f"mismatch={report.mismatch_count}",
            f"no_home={report.no_home_count}",
            f"bad_path={report.bad_path_count}",
            f"orphan={len(report.orphaned_homes)}",
            f"quota_warn={len(report.quota_warn_users)}",
            f"quota_crit={len(report.quota_crit_users)}",
        ]
        if report.check_perms_enabled:
            parts_msg.append(f"perm_warn={len(report.permission_warnings)}")

        extra_details: list[str] = []
        if report.quota_crit_users:
            extra_details.append(f"CRIT storage: {', '.join(report.quota_crit_users)}")
        if report.quota_warn_users:
            extra_details.append(f"WARN storage: {', '.join(report.quota_warn_users)}")

        perfdata = (
            f"ok={len(report.present_homes)} missing={len(report.missing_homes)} "
            f"locked={report.locked_total_count} mismatch={report.mismatch_count} "
            f"no_home={report.no_home_count} bad_path={report.bad_path_count} "
            f"orphan={len(report.orphaned_homes)} quota_warn={len(report.quota_warn_users)} "
            f"quota_crit={len(report.quota_crit_users)} perm_warn={len(report.permission_warnings)}"
        )
        summary_text = ", ".join(parts_msg)
        if extra_details:
            summary_text += f" ({'; '.join(extra_details)})"
        print(f"{state} - ZFS Homes ({report.pool_path}): {summary_text} | {perfdata}")
        return rc

    def render_error(self, exc: Exception) -> int:
        print(f"UNKNOWN - {exc}")
        return EXIT_UNKNOWN


class JsonReporter(CheckReporter):
    """Machine-readable JSON output on stdout."""

    def render_single(self, report: SingleCheckReport) -> int:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK

    def render_all(self, report: AllHomesReport) -> int:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK

    def render_error(self, exc: Exception) -> int:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1


def format_bytes(num_bytes: int) -> str:
    """Formats byte counts using binary units (KiB, MiB, GiB, TiB)."""
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    val = float(num_bytes)
    for unit in units:
        if abs(val) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(val)} B"
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{num_bytes} B"


def evaluate_storage_status(
    used_bytes: int,
    quota_bytes: int | None,
    warn_pct: float,
    crit_pct: float,
) -> tuple[float | None, str]:
    if quota_bytes is None or quota_bytes <= 0:
        return None, "NO_QUOTA"
    pct = (used_bytes / quota_bytes) * 100.0
    if pct >= crit_pct:
        return pct, "CRIT"
    if pct >= warn_pct:
        return pct, "WARN"
    return pct, "OK"


def run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise CommandError(
            f"Command failed ({' '.join(args)}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def require_ipa_cli() -> None:
    if not shutil.which("ipa"):
        raise CommandError("Command 'ipa' was not found in PATH.")


def dataset_exists(dataset: str) -> bool:
    proc = run_cmd(["zfs", "list", "-H", "-o", "name", dataset], check=False)
    return proc.returncode == 0


def get_dataset_usage(
    dataset: str,
    warn_pct: float = DEFAULT_WARN_PCT,
    crit_pct: float = DEFAULT_CRIT_PCT,
) -> StorageUsageInfo | None:
    """Reads `used` and `quota` in bytes for a single ZFS dataset."""
    proc = run_cmd(["zfs", "list", "-H", "-p", "-o", "used,quota", dataset], check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    parts = proc.stdout.strip().splitlines()[0].split("\t")
    if len(parts) < 2:
        parts = proc.stdout.strip().splitlines()[0].split()
    if len(parts) < 2:
        return None

    try:
        used_bytes = int(parts[0].strip())
    except ValueError as exc:
        raise CommandError(f"Invalid ZFS 'used' value for '{dataset}': '{parts[0]}'") from exc

    quota_raw = parts[1].strip().lower()
    quota_bytes: int | None
    if quota_raw in {"", "0", "none"}:
        quota_bytes = None
    else:
        try:
            q_val = int(quota_raw)
            quota_bytes = q_val if q_val > 0 else None
        except ValueError as exc:
            raise CommandError(f"Invalid ZFS 'quota' value for '{dataset}': '{parts[1]}'") from exc

    pct, status = evaluate_storage_status(used_bytes, quota_bytes, warn_pct, crit_pct)
    return StorageUsageInfo(
        dataset=dataset,
        used_bytes=used_bytes,
        quota_bytes=quota_bytes,
        usage_pct=pct,
        status=status,
    )


def list_home_datasets_usage(
    pool_path: str,
    warn_pct: float = DEFAULT_WARN_PCT,
    crit_pct: float = DEFAULT_CRIT_PCT,
) -> dict[str, StorageUsageInfo]:
    """Returns {username: StorageUsageInfo} for all direct child home datasets under pool_path."""
    proc = run_cmd(["zfs", "list", "-H", "-p", "-r", "-o", "name,used,quota", pool_path], check=False)
    if proc.returncode != 0:
        return {}

    result: dict[str, StorageUsageInfo] = {}
    prefix = f"{pool_path.rstrip('/')}/"
    for line in proc.stdout.splitlines():
        raw = line.strip()
        if not raw:
            continue
        cols = raw.split("\t") if "\t" in raw else raw.split()
        if len(cols) < 3:
            continue
        name, used_str, quota_str = cols[0].strip(), cols[1].strip(), cols[2].strip().lower()
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix) :]
        if not rel or "/" in rel:
            continue

        try:
            used_bytes = int(used_str)
        except ValueError:
            continue

        quota_bytes: int | None
        if quota_str in {"", "0", "none"}:
            quota_bytes = None
        else:
            try:
                q_val = int(quota_str)
                quota_bytes = q_val if q_val > 0 else None
            except ValueError:
                quota_bytes = None

        pct, status = evaluate_storage_status(used_bytes, quota_bytes, warn_pct, crit_pct)
        result[rel] = StorageUsageInfo(
            dataset=name,
            used_bytes=used_bytes,
            quota_bytes=quota_bytes,
            usage_pct=pct,
            status=status,
        )
    return result


def parse_raw_block(block: str) -> dict[str, str]:
    """Parses a raw record block from `ipa user-find --raw` or `ipa user-show --raw`."""
    entry: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key not in entry:
            entry[key] = val
    return entry


def parse_ipa_raw_users(output: str, min_uid: int | None = None) -> list[IpaUserRecord]:
    """Parses `ipa user-find --all --raw --sizelimit=0` output into user records."""
    records: list[IpaUserRecord] = []
    current_lines: list[str] = []

    def finalize_block(lines: list[str]) -> None:
        if not lines:
            return
        entry = parse_raw_block("\n".join(lines))
        username = entry.get("uid", "").strip()
        if not username:
            return
        if min_uid is not None and "uidnumber" in entry:
            try:
                if int(entry["uidnumber"].strip()) < min_uid:
                    return
            except ValueError:
                pass

        locked = entry.get("nsaccountlock", "false").strip().lower() == "true"
        home_dir = entry.get("homedirectory", "").strip() or None
        records.append(
            IpaUserRecord(
                uid=username,
                home_directory=home_dir,
                locked=locked,
            )
        )

    for raw_line in output.splitlines():
        if not raw_line.strip():
            finalize_block(current_lines)
            current_lines = []
        else:
            current_lines.append(raw_line)

    finalize_block(current_lines)
    return records


def get_freeipa_users(min_uid: int | None = None) -> list[IpaUserRecord]:
    """Fetches FreeIPA users via `ipa user-find --all --raw --sizelimit=0`."""
    require_ipa_cli()
    proc = run_cmd(
        ["ipa", "user-find", "--all", "--raw", "--sizelimit=0"],
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return parse_ipa_raw_users(proc.stdout, min_uid=min_uid)
    raise CommandError(
        f"`ipa user-find` failed: {proc.stderr.strip() or proc.stdout.strip()}"
    )


def get_single_ipa_record(username: str) -> IpaUserRecord | None:
    """Fetches a single user from FreeIPA. Returns None if not found."""
    require_ipa_cli()
    proc = run_cmd(["ipa", "user-show", username, "--all", "--raw"], check=False)
    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout).lower()
        if "not found" in err_msg:
            return None
        raise CommandError(
            f"`ipa user-show {username}` failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    entry = parse_raw_block(proc.stdout)
    uid = entry.get("uid", "").strip() or username
    locked = entry.get("nsaccountlock", "false").strip().lower() == "true"
    home_dir = entry.get("homedirectory", "").strip() or None
    return IpaUserRecord(uid=uid, home_directory=home_dir, locked=locked)


def validate_user_homedirectory(username: str, home_directory: str | None, home_prefix: str) -> None:
    """Ensures homedirectory is set and equals `<home_prefix>/<username>`."""
    expected_path = f"{home_prefix.rstrip('/')}/{username}"
    if not home_directory:
        raise CommandError(
            f"User '{username}' has no 'homedirectory' set (expected: {expected_path}). "
            f"Use --ignore-homedirectory to override."
        )

    userdir = extract_remote_userdir(home_directory, home_prefix)
    if userdir is None:
        raise CommandError(
            f"User '{username}' has an invalid 'homedirectory' ('{home_directory}'). "
            f"Expected '{expected_path}'. Use --ignore-homedirectory to override."
        )

    if userdir != username:
        raise CommandError(
            f"Conflict for '{username}': 'homedirectory' is '{home_directory}' (userdir='{userdir}'), "
            f"which does not match uid '{username}'. Use --ignore-homedirectory to override."
        )


def classify_users(records: list[IpaUserRecord], home_prefix: str) -> ClassifiedUsers:
    """Classifies users based on homedirectory, <home_prefix>/<userdir>, and uid."""
    active_valid: dict[str, IpaUserRecord] = {}
    locked_valid: dict[str, IpaUserRecord] = {}
    missing_home_attr: dict[str, IpaUserRecord] = {}
    invalid_home_path: dict[str, IpaUserRecord] = {}
    mismatched_home: dict[str, IpaUserRecord] = {}

    for rec in records:
        if not rec.home_directory:
            missing_home_attr[rec.uid] = rec
            continue

        userdir = rec.remote_userdir(home_prefix)
        if userdir is None:
            invalid_home_path[rec.uid] = rec
            continue

        if userdir != rec.uid:
            mismatched_home[rec.uid] = rec
            continue

        if rec.locked:
            locked_valid[rec.uid] = rec
        else:
            active_valid[rec.uid] = rec

    return ClassifiedUsers(
        active_valid=active_valid,
        locked_valid=locked_valid,
        missing_home_attr=missing_home_attr,
        invalid_home_path=invalid_home_path,
        mismatched_home=mismatched_home,
    )


def get_uid_gid(username: str) -> tuple[int, int]:
    try:
        pw = pwd.getpwnam(username)
        return pw.pw_uid, pw.pw_gid
    except KeyError as exc:
        raise CommandError(f"User '{username}' does not exist in system/FreeIPA.") from exc


def try_get_uid_gid(username: str) -> tuple[int, int] | None:
    try:
        pw = pwd.getpwnam(username)
        return pw.pw_uid, pw.pw_gid
    except KeyError:
        return None


def check_directory_permissions(
    home_dir: Path,
    username: str,
    expected_mode: int = DEFAULT_MODE,
) -> PermissionCheckResult:
    """Checks ownership (UID/GID) and permissions of a home directory."""
    uid_gid = try_get_uid_gid(username)
    expected_uid = uid_gid[0] if uid_gid else None
    expected_gid = uid_gid[1] if uid_gid else None
    issues: list[str] = []

    if not home_dir.exists():
        issues.append(f"Directory '{home_dir}' does not exist")
        return PermissionCheckResult(
            ok=False,
            path=home_dir,
            expected_uid=expected_uid,
            expected_gid=expected_gid,
            expected_mode=expected_mode,
            issues=issues,
        )

    st = home_dir.stat()
    actual_uid = st.st_uid
    actual_gid = st.st_gid
    actual_mode = stat.S_IMODE(st.st_mode)

    if expected_uid is None or expected_gid is None:
        issues.append(
            f"User '{username}' cannot be resolved via NSS/passwd (actual={actual_uid}:{actual_gid})"
        )
    else:
        if actual_uid != expected_uid:
            issues.append(f"UID is {actual_uid}, expected {expected_uid}")
        if actual_gid != expected_gid:
            issues.append(f"GID is {actual_gid}, expected {expected_gid}")

    if actual_mode != expected_mode:
        issues.append(f"Mode is {oct(actual_mode)}, expected {oct(expected_mode)}")

    return PermissionCheckResult(
        ok=len(issues) == 0,
        path=home_dir,
        actual_uid=actual_uid,
        expected_uid=expected_uid,
        actual_gid=actual_gid,
        expected_gid=expected_gid,
        actual_mode=actual_mode,
        expected_mode=expected_mode,
        issues=issues,
    )


def fix_directory_permissions(
    home_dir: Path,
    username: str,
    perm: PermissionCheckResult,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Fixes mismatched UID:GID and mode on `home_dir`. Returns (fixed, message)."""
    if not home_dir.exists():
        return False, f"Directory '{home_dir}' does not exist"

    uid_gid = try_get_uid_gid(username)
    if uid_gid is None:
        return False, f"User '{username}' cannot be resolved via NSS/passwd"

    target_uid, target_gid = uid_gid
    target_mode = perm.expected_mode
    changes: list[str] = []

    if perm.actual_uid != target_uid or perm.actual_gid != target_gid:
        changes.append(f"chown {target_uid}:{target_gid}")
        if not dry_run:
            try:
                os.chown(home_dir, target_uid, target_gid)
            except OSError as exc:
                raise CommandError(f"chown failed for '{home_dir}': {exc}") from exc

    if perm.actual_mode != target_mode:
        changes.append(f"chmod {oct(target_mode)}")
        if not dry_run:
            try:
                os.chmod(home_dir, target_mode)
            except OSError as exc:
                raise CommandError(f"chmod failed for '{home_dir}': {exc}") from exc

    if not changes:
        return True, "no changes required"

    prefix = "[DRY-RUN] " if dry_run else "[FIXED] "
    return True, f"{prefix}{', '.join(changes)} on '{home_dir}'"


def create_home(
    username: str,
    quota: str = DEFAULT_QUOTA,
    pool_path: str = POOL_PATH,
    mount_base: Path = MOUNT_BASE,
    skel_dir: Path = SKEL_DIR,
    home_prefix: str = HOME_PREFIX,
    ignore_homedirectory: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
) -> None:
    dataset = f"{pool_path.rstrip('/')}/{username}"
    home_dir = mount_base / username

    if dataset_exists(dataset):
        if not quiet:
            print(f"[SKIP] Dataset '{dataset}' already exists.")
        return

    if not ignore_homedirectory:
        rec = get_single_ipa_record(username)
        home_directory = rec.home_directory if rec else None
        validate_user_homedirectory(username, home_directory, home_prefix=home_prefix)

    uid_num, gid_num = get_uid_gid(username)

    if dry_run:
        if not quiet:
            print(
                f"[DRY-RUN] Would create: {dataset} (quota={quota}, UID={uid_num}, GID={gid_num})"
            )
        return

    run_cmd(["zfs", "create", "-o", f"quota={quota}", dataset])

    if not home_dir.exists():
        home_dir.mkdir(parents=True, exist_ok=True)

    if skel_dir.exists():
        shutil.copytree(skel_dir, home_dir, dirs_exist_ok=True)

    run_cmd(["chown", "-R", f"{uid_num}:{gid_num}", str(home_dir)])
    run_cmd(["chmod", f"{DEFAULT_MODE:o}", str(home_dir)])
    if not quiet:
        print(
            f"[OK] ZFS home for '{username}' (UID: {uid_num}, GID: {gid_num}, quota: {quota}) created."
        )


def destroy_home(
    username: str,
    pool_path: str = POOL_PATH,
    force: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
) -> None:
    dataset = f"{pool_path.rstrip('/')}/{username}"

    if not dataset_exists(dataset):
        if not quiet:
            print(f"[SKIP] Dataset '{dataset}' does not exist.")
        return

    if dry_run:
        if not quiet:
            print(f"[DRY-RUN] Would destroy dataset '{dataset}' (-r).")
        return

    if not force:
        answer = (
            input(f"Really destroy dataset '{dataset}' and all its data? [yes/NO]: ")
            .strip()
            .lower()
        )
        if answer not in {"yes", "y"}:
            if not quiet:
                print("[ABORTED] Destroy aborted.")
            return

    run_cmd(["zfs", "destroy", "-r", dataset])
    if not quiet:
        print(f"[OK] Dataset '{dataset}' has been completely removed.")


def format_usage_summary(usage: StorageUsageInfo | None) -> str:
    if usage is None:
        return "n/a"
    used_str = format_bytes(usage.used_bytes)
    if usage.quota_bytes is None or usage.usage_pct is None:
        return f"{used_str} used (no quota)"
    quota_str = format_bytes(usage.quota_bytes)
    return f"{used_str} / {quota_str} ({usage.usage_pct:.1f}%) [{usage.status}]"


def build_single_check_report(
    username: str,
    pool_path: str = POOL_PATH,
    mount_base: Path = MOUNT_BASE,
    home_prefix: str = HOME_PREFIX,
    expected_mode: int = DEFAULT_MODE,
    warn_pct: float = DEFAULT_WARN_PCT,
    crit_pct: float = DEFAULT_CRIT_PCT,
    fix_perms: bool = False,
    dry_run: bool = False,
) -> SingleCheckReport:
    """Collects dataset, IPA status, action, permissions, and storage usage for one user."""
    dataset = f"{pool_path.rstrip('/')}/{username}"
    home_dir = mount_base / username
    usage = (
        get_dataset_usage(dataset, warn_pct=warn_pct, crit_pct=crit_pct)
        if dataset_exists(dataset)
        else None
    )
    ds_exists = usage is not None
    rec = get_single_ipa_record(username)
    expected_home = f"{home_prefix.rstrip('/')}/{username}"

    user_exists = rec is not None
    is_locked = rec.locked if rec else False
    home_attr = rec.home_directory if rec else None
    extracted_userdir = rec.remote_userdir(home_prefix) if rec else None

    valid_active_home = (
        user_exists
        and not is_locked
        and home_attr is not None
        and extracted_userdir == username
    )
    mismatch_detected = user_exists and extracted_userdir is not None and extracted_userdir != username

    if not user_exists:
        if ds_exists:
            action_code = "DESTROY_CANDIDATE"
            action_detail = "DESTROY_CANDIDATE (User no longer exists, dataset present)"
        else:
            action_code = "NONE"
            action_detail = "NONE (Neither user nor dataset exists)"
    elif mismatch_detected:
        action_code = "CONFLICT_MANUAL"
        action_detail = (
            f"CONFLICT_MANUAL (homedirectory='{home_attr}' differs from uid '{username}')"
        )
    elif is_locked:
        if ds_exists:
            action_code = "KEEP_LOCKED"
            action_detail = "KEEP_LOCKED (Account locked, keep dataset)"
        else:
            action_code = "SKIP_LOCKED"
            action_detail = "SKIP_LOCKED (Account locked, do not create dataset)"
    else:
        if ds_exists:
            action_code = "OK"
            action_detail = "OK (Dataset present)"
        elif valid_active_home:
            action_code = "CREATE_NEEDED"
            action_detail = "CREATE_NEEDED (Active user with valid homedirectory, dataset missing)"
        elif not home_attr:
            action_code = "NO_HOME_ATTR"
            action_detail = f"NO_HOME_ATTR (No homedirectory set, expected {expected_home})"
        else:
            action_code = "BAD_HOME_PATH"
            action_detail = f"BAD_HOME_PATH (homedirectory='{home_attr}', expected '{expected_home}')"

    perm_result: PermissionCheckResult | None = None
    perm_fix_msg: str | None = None

    if home_dir.exists():
        perm = check_directory_permissions(home_dir, username=username, expected_mode=expected_mode)
        perm_result = perm
        if not perm.ok and fix_perms:
            fixed, fix_msg = fix_directory_permissions(
                home_dir=home_dir,
                username=username,
                perm=perm,
                dry_run=dry_run,
            )
            perm_fix_msg = fix_msg
            if fixed and not dry_run:
                perm_result = check_directory_permissions(
                    home_dir, username=username, expected_mode=expected_mode
                )

    return SingleCheckReport(
        username=username,
        pool_path=pool_path,
        home_prefix=home_prefix,
        expected_home_directory=expected_home,
        dataset=dataset,
        dataset_exists=ds_exists,
        directory=home_dir,
        directory_exists=home_dir.exists(),
        user_record=rec,
        action_code=action_code,
        action_detail=action_detail,
        warn_pct=warn_pct,
        crit_pct=crit_pct,
        storage=usage,
        permissions=perm_result,
        permission_fix_message=perm_fix_msg,
    )


def build_all_homes_report(
    pool_path: str = POOL_PATH,
    mount_base: Path = MOUNT_BASE,
    skel_dir: Path = SKEL_DIR,
    home_prefix: str = HOME_PREFIX,
    quota: str = DEFAULT_QUOTA,
    warn_pct: float = DEFAULT_WARN_PCT,
    crit_pct: float = DEFAULT_CRIT_PCT,
    do_create: bool = False,
    do_destroy: bool = False,
    check_perms: bool = False,
    fix_perms: bool = False,
    expected_mode: int = DEFAULT_MODE,
    force: bool = False,
    dry_run: bool = False,
    min_uid: int | None = None,
    quiet_mutations: bool = False,
) -> AllHomesReport:
    """Reconciles all FreeIPA users and ZFS home datasets."""
    should_check_perms = check_perms or fix_perms
    records = get_freeipa_users(min_uid=min_uid)
    classified = classify_users(records, home_prefix=home_prefix)
    dataset_usages = list_home_datasets_usage(pool_path, warn_pct=warn_pct, crit_pct=crit_pct)

    active_set = set(classified.active_valid.keys())
    locked_set = set(classified.locked_valid.keys())
    ds_set = set(dataset_usages.keys())

    protected_names: set[str] = set()
    for rec in records:
        protected_names.add(rec.uid)
        ud = rec.remote_userdir(home_prefix)
        if ud:
            protected_names.add(ud)

    present_homes = sorted(active_set & ds_set)
    missing_homes = sorted(active_set - ds_set)
    locked_with_home = sorted(locked_set & ds_set)
    locked_without_home = sorted(locked_set - ds_set)
    orphaned_homes = sorted(ds_set - protected_names)

    quota_warn_users: list[str] = []
    quota_crit_users: list[str] = []
    for name in sorted(ds_set):
        u_info = dataset_usages[name]
        pct_str = f"{u_info.usage_pct:.1f}%" if u_info.usage_pct is not None else "n/a"
        if u_info.status == "CRIT":
            quota_crit_users.append(f"{name} ({pct_str})")
        elif u_info.status == "WARN":
            quota_warn_users.append(f"{name} ({pct_str})")

    perm_warnings: list[str] = []
    perm_actions: list[str] = []
    perm_results_map: dict[str, tuple[PermissionCheckResult, str | None]] = {}
    if should_check_perms:
        for user in present_homes + locked_with_home:
            home_dir = mount_base / user
            perm = check_directory_permissions(
                home_dir, username=user, expected_mode=expected_mode
            )
            if not perm.ok:
                if fix_perms:
                    fixed, msg = fix_directory_permissions(
                        home_dir=home_dir,
                        username=user,
                        perm=perm,
                        dry_run=dry_run,
                    )
                    perm_actions.append(f"{user}: {msg}")
                    if fixed and not dry_run:
                        recheck = check_directory_permissions(
                            home_dir, username=user, expected_mode=expected_mode
                        )
                        perm_results_map[user] = (recheck, msg)
                        if not recheck.ok:
                            perm_warnings.append(f"{user}: {', '.join(recheck.issues or [])}")
                    else:
                        perm_results_map[user] = (perm, msg)
                        perm_warnings.append(f"{user}: {', '.join(perm.issues or [])}")
                else:
                    perm_results_map[user] = (perm, None)
                    perm_warnings.append(f"{user}: {', '.join(perm.issues or [])}")
            else:
                perm_results_map[user] = (perm, None)

    created_homes: list[str] = []
    destroyed_homes: list[str] = []

    if do_create:
        for user in missing_homes:
            create_home(
                username=user,
                quota=quota,
                pool_path=pool_path,
                mount_base=mount_base,
                skel_dir=skel_dir,
                home_prefix=home_prefix,
                dry_run=dry_run,
                quiet=quiet_mutations,
            )
            created_homes.append(user)

    if do_destroy:
        for name in orphaned_homes:
            destroy_home(
                username=name,
                pool_path=pool_path,
                force=force,
                dry_run=dry_run,
                quiet=quiet_mutations,
            )
            destroyed_homes.append(name)

    return AllHomesReport(
        pool_path=pool_path,
        mount_base=mount_base,
        home_prefix=home_prefix,
        dry_run=dry_run,
        warn_pct=warn_pct,
        crit_pct=crit_pct,
        check_perms_enabled=should_check_perms,
        classified=classified,
        dataset_usages=dataset_usages,
        present_homes=present_homes,
        missing_homes=missing_homes,
        locked_with_home=locked_with_home,
        locked_without_home=locked_without_home,
        orphaned_homes=orphaned_homes,
        quota_warn_users=quota_warn_users,
        quota_crit_users=quota_crit_users,
        permission_warnings=perm_warnings,
        permission_actions=perm_actions,
        permission_results=perm_results_map,
        created_homes=created_homes,
        destroyed_homes=destroyed_homes,
    )


def create_reporter(icinga: bool = False, as_json: bool = False) -> CheckReporter:
    if as_json:
        return JsonReporter()
    if icinga:
        return IcingaReporter()
    return TextReporter()


def parse_octal_mode(value: str) -> int:
    try:
        mode = int(value, 8)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid octal mode '{value}' (e.g. 700 or 0750)."
        ) from exc
    if mode <= 0 or mode > 0o7777:
        raise argparse.ArgumentTypeError(f"Mode out of valid range: '{value}'.")
    return mode


def parse_threshold_pct(value: str) -> float:
    try:
        pct = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid percentage value '{value}'.") from exc
    if pct <= 0.0 or pct > 100.0:
        raise argparse.ArgumentTypeError(f"Threshold must be between 0 and 100: '{value}'.")
    return pct


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage ZFS home datasets for FreeIPA users.",
        epilog=TECHNICAL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pool", default=POOL_PATH, help=f"ZFS base dataset (default: {POOL_PATH})")
    parser.add_argument(
        "--mount-base", type=Path, default=MOUNT_BASE, help=f"Mount base directory (default: {MOUNT_BASE})"
    )
    parser.add_argument("--skel", type=Path, default=SKEL_DIR, help=f"Skeleton directory (default: {SKEL_DIR})")
    parser.add_argument(
        "--home-prefix", default=HOME_PREFIX, help=f"Expected IPA home base path (default: {HOME_PREFIX})"
    )
    parser.add_argument(
        "--mode",
        type=parse_octal_mode,
        default=DEFAULT_MODE,
        help="Expected directory mode in octal (default: 700)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show changes without executing them")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="Create a ZFS home dataset for a user")
    p_create.add_argument("user", help="Username")
    p_create.add_argument("--quota", default=DEFAULT_QUOTA, help=f"ZFS quota (default: {DEFAULT_QUOTA})")
    p_create.add_argument(
        "--ignore-homedirectory",
        action="store_true",
        help="Skip checking the FreeIPA homedirectory attribute",
    )

    p_destroy = subparsers.add_parser(
        "destroy", help="Permanently remove a user's ZFS home dataset"
    )
    p_destroy.add_argument("user", help="Username")
    p_destroy.add_argument("-f", "--force", action="store_true", help="Destroy without confirmation prompt")

    p_check = subparsers.add_parser(
        "check",
        help="Check a single user (with [user]) or reconcile all users (without [user])",
    )
    p_check.add_argument("user", nargs="?", default=None, help="Optional username for single-user check")
    p_check.add_argument(
        "--warn-pct",
        type=parse_threshold_pct,
        default=DEFAULT_WARN_PCT,
        help=f"Warning threshold for quota usage in %% (default: {DEFAULT_WARN_PCT:g})",
    )
    p_check.add_argument(
        "--crit-pct",
        type=parse_threshold_pct,
        default=DEFAULT_CRIT_PCT,
        help=f"Critical threshold for quota usage in %% (default: {DEFAULT_CRIT_PCT:g})",
    )
    p_check.add_argument(
        "--create", action="store_true", help="Create missing home directories for active users"
    )
    p_check.add_argument("--destroy", action="store_true", help="Destroy orphaned home directories")
    p_check.add_argument(
        "--check-perms", action="store_true", help="Check ownership (UID:GID) and permissions on existing homes"
    )
    p_check.add_argument("--fix-perms", action="store_true", help="Fix mismatched UID:GID and permissions")
    p_check.add_argument(
        "--quota", default=DEFAULT_QUOTA, help=f"Quota for newly created homes (default: {DEFAULT_QUOTA})"
    )
    p_check.add_argument("-f", "--force", action="store_true", help="Destroy orphaned homes without confirmation prompt")
    p_check.add_argument("--min-uid", type=int, default=None, help="Only include users with UID at or above this value")

    mode_group = p_check.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--icinga",
        "--nagios",
        dest="icinga",
        action="store_true",
        help="Output in Icinga2/Nagios plugin format with matching exit code (mutually exclusive with --json)",
    )
    mode_group.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output as JSON (mutually exclusive with --icinga / --nagios)",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    is_icinga = bool(getattr(args, "icinga", False))
    is_json = bool(getattr(args, "as_json", False))
    reporter = create_reporter(icinga=is_icinga, as_json=is_json)

    try:
        if args.command == "create":
            create_home(
                username=args.user,
                quota=args.quota,
                pool_path=args.pool,
                mount_base=args.mount_base,
                skel_dir=args.skel,
                home_prefix=args.home_prefix,
                ignore_homedirectory=args.ignore_homedirectory,
                dry_run=args.dry_run,
            )
            return EXIT_OK
        if args.command == "destroy":
            destroy_home(
                username=args.user,
                pool_path=args.pool,
                force=args.force,
                dry_run=args.dry_run,
            )
            return EXIT_OK
        if args.command == "check":
            if args.crit_pct < args.warn_pct:
                raise CommandError(
                    f"--crit-pct ({args.crit_pct:g}) must not be less than --warn-pct ({args.warn_pct:g})."
                )
            if args.user:
                single_report = build_single_check_report(
                    username=args.user,
                    pool_path=args.pool,
                    mount_base=args.mount_base,
                    home_prefix=args.home_prefix,
                    expected_mode=args.mode,
                    warn_pct=args.warn_pct,
                    crit_pct=args.crit_pct,
                    fix_perms=args.fix_perms,
                    dry_run=args.dry_run,
                )
                return reporter.render_single(single_report)

            all_report = build_all_homes_report(
                pool_path=args.pool,
                mount_base=args.mount_base,
                skel_dir=args.skel,
                home_prefix=args.home_prefix,
                quota=args.quota,
                warn_pct=args.warn_pct,
                crit_pct=args.crit_pct,
                do_create=args.create,
                do_destroy=args.destroy,
                check_perms=args.check_perms,
                fix_perms=args.fix_perms,
                expected_mode=args.mode,
                force=args.force,
                dry_run=args.dry_run,
                min_uid=args.min_uid,
                quiet_mutations=is_json,
            )
            return reporter.render_all(all_report)
        return EXIT_UNKNOWN
    except CommandError as exc:
        return reporter.render_error(exc)


if __name__ == "__main__":
    sys.exit(main())
