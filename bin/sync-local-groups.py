#!/usr/bin/env python3
"""
sync-local-groups.py
--------------------
Synchronizes local system groups (/etc/group) for centrally managed
users (e.g., FreeIPA) upon login via PAM (pam_exec) or manual CLI execution.

How it works:
  - Reads rules from /etc/security/ipa-local-groups.conf (or via -c).
  - Format: <user> <ipa-group> <local-groups>
    * Supports wildcards (*), comma-separated lists (a,b), and negations (!x).
    * Evaluates all matching rules top-to-bottom ("Last Match Wins").
  - Adds missing local groups via 'usermod -aG' and removes explicitly
    negated groups (!group) via 'gpasswd -d'.
  - Silently ignores groups that do not exist on the local host.
  - Logs any applied membership changes to syslog (LOG_INFO).

Usage:
  - PAM (pam_exec): session optional pam_exec.so /usr/local/bin/sync-local-groups.py
  - CLI / Dry-Run : sync-local-groups.py -n -u <username>
"""

import argparse
import grp
import os
import pwd
import shutil
import subprocess
import sys
import syslog

CONFIG_FILE = "/etc/security/ipa-local-groups.conf"
SEARCH_PATH = "/usr/sbin:/sbin:/usr/bin:/bin"


def get_all_effective_groups(username: str) -> set[str]:
    """Return all effective groups (FreeIPA + local) resolved via NSS."""
    pw = pwd.getpwnam(username)
    gids = os.getgrouplist(username, pw.pw_gid)
    groups = set()
    for gid in gids:
        try:
            groups.add(grp.getgrgid(gid).gr_name)
        except KeyError:
            pass
    return groups


def get_local_groups_info(username: str) -> tuple[set[str], set[str]]:
    """
    Read the local /etc/group file exclusively:
    - all_local: All local group names existing on this host
    - user_local: Local groups in which 'username' is currently an explicit member
    """
    all_local: set[str] = set()
    user_local: set[str] = set()
    for g in grp.getgrall():
        all_local.add(g.gr_name)
        if username in g.gr_mem:
            user_local.add(g.gr_name)
    return all_local, user_local


def matches_rule(pattern: str, candidates: set[str]) -> bool:
    if pattern == "*":
        return True
    items = [item.strip() for item in pattern.split(",") if item.strip()]
    negated = {item[1:] for item in items if item.startswith("!")}
    positive = {item for item in items if not item.startswith("!")}

    if negated & candidates:
        return False
    return bool(not positive or (positive & candidates))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Synchronize local Linux groups based on /etc/security/ipa-local-groups.conf"
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Show which groups would be added or removed without making changes"
    )
    parser.add_argument(
        "-c", "--config",
        default=CONFIG_FILE,
        help=f"Path to configuration file (default: {CONFIG_FILE})"
    )
    parser.add_argument(
        "-u", "--user",
        default="",
        help="Target username for manual CLI execution (defaults to PAM_USER if omitted)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Determine mode: explicit CLI invocation (-u) vs. PAM session hook
    if args.user:
        target_user = args.user.strip()
        is_cli = True
    else:
        if os.environ.get("PAM_TYPE") != "open_session":
            return 0
        target_user = os.environ.get("PAM_USER", "").strip()
        is_cli = False

    if not target_user or not os.path.isfile(args.config):
        if is_cli and not os.path.isfile(args.config):
            print(f"Error: Configuration file {args.config} not found.", file=sys.stderr)
            return 1
        return 0

    try:
        effective_groups = get_all_effective_groups(target_user)
    except KeyError:
        if is_cli:
            print(f"Error: User '{target_user}' does not exist.", file=sys.stderr)
            return 1
        return 0

    all_local_groups, user_local_groups = get_local_groups_info(target_user)

    # desired_state: group_name -> True (add) | False (remove)
    desired_state: dict[str, bool] = {}

    with open(args.config, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue

            parts = line.split(maxsplit=2)
            if len(parts) < 3:
                continue

            cfg_user, cfg_group, cfg_autogroups = parts

            if not matches_rule(cfg_user, {target_user}):
                continue
            if not matches_rule(cfg_group, effective_groups):
                continue

            for target in cfg_autogroups.split(","):
                target = target.strip()
                if not target:
                    continue
                if target.startswith("!"):
                    grp_name = target[1:].strip()
                    if grp_name in all_local_groups:
                        desired_state[grp_name] = False
                else:
                    if target in all_local_groups:
                        desired_state[target] = True

    to_add = sorted(g for g, state in desired_state.items() if state and g not in user_local_groups)
    to_remove = sorted(g for g, state in desired_state.items() if not state and g in user_local_groups)

    if args.dry_run:
        ipa_only_groups = sorted(effective_groups - user_local_groups)
        print(f"Dry-run for user     : {target_user}")
        print(f"  FreeIPA/Primary gr.: {', '.join(ipa_only_groups) if ipa_only_groups else '(none)'}")
        print(f"  Local /etc/group   : {', '.join(sorted(user_local_groups)) if user_local_groups else '(none)'}")
        print(f"  To add (+)         : {', '.join(to_add) if to_add else '(none)'}")
        print(f"  To remove (-)      : {', '.join(to_remove) if to_remove else '(none)'}")
        return 0

    usermod_bin = shutil.which("usermod", path=SEARCH_PATH) or "/usr/sbin/usermod"
    gpasswd_bin = shutil.which("gpasswd", path=SEARCH_PATH) or "/usr/bin/gpasswd"

    if to_add:
        subprocess.run(
            [usermod_bin, "-aG", ",".join(to_add), target_user],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        msg = f"sync-local-groups: added {target_user} to {','.join(to_add)}"
        syslog.syslog(syslog.LOG_INFO, msg)
        if is_cli:
            print(msg)

    for grp_name in to_remove:
        subprocess.run(
            [gpasswd_bin, "-d", target_user, grp_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        msg = f"sync-local-groups: removed {target_user} from {grp_name}"
        syslog.syslog(syslog.LOG_INFO, msg)
        if is_cli:
            print(msg)

    if is_cli and not to_add and not to_remove:
        print(f"No changes required for '{target_user}'.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
