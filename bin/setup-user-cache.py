#!/usr/bin/env python3
"""
Description:
Resolves a local, writable cache directory for users under a configured
remote home prefix and ensures that $HOME/.cache points to it as a valid
symlink.

Required arguments:
--remote-home-prefix : Prefix for remote home directories (e.g. /home/remote/)
--admin-cache-base   : Base directory for user caches (e.g. /var/cache/users)

Optional arguments:
--fallback-cache-base : May be passed multiple times; defaults to /var/tmp
                        and /tmp.

Workflow:
1. Checks whether $HOME starts with --remote-home-prefix; exits immediately
   otherwise.
2. Attempts to create <admin-cache-base>/<username> and verifies ownership
   (UID) as well as write permissions.
3. Falls back to directories under the fallback base directories if
   permissions are missing or ownership does not match.
4. Repairs broken or wrong symlinks or moves existing directories/files at
   $HOME/.cache aside, creates the symlink, and prints the target path to
   stdout.
"""

import argparse
import getpass
import os
import shutil
import sys
import time
from pathlib import Path

DEFAULT_FALLBACK_CACHE_BASES = (
    Path("/var/tmp"),
    Path("/tmp"),
)
CACHE_DIR_NAME = ".cache"
STALE_CACHE_PREFIX = ".cache.stale"
FALLBACK_DIR_PREFIX = "cache"
CACHE_DIR_MODE = 0o700


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up a local XDG cache directory and the ~/.cache symlink."
    )
    parser.add_argument(
        "--remote-home-prefix",
        required=True,
        help="Prefix for remote home directories, e.g. /home/remote/",
    )
    parser.add_argument(
        "--admin-cache-base",
        required=True,
        type=Path,
        help="Base directory for user caches, e.g. /var/cache/users",
    )
    parser.add_argument(
        "--fallback-cache-base",
        action="append",
        dest="fallback_cache_bases",
        type=Path,
        default=None,
        help="Optional fallback base directory (can be specified multiple times; default: /var/tmp and /tmp)",
    )
    return parser.parse_args(argv)


def is_owned_and_writable_dir(path: Path, uid: int) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        st = path.stat()
    except OSError:
        return False

    if st.st_uid != uid:
        return False

    probe = path / f".probe.{os.getpid()}.{uid}"
    try:
        probe.touch(exist_ok=False)
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def resolve_local_cache(
    username: str,
    uid: int,
    admin_cache_base: Path,
    fallback_cache_bases: tuple[Path, ...],
) -> Path:
    admin_dir = admin_cache_base / username
    try:
        admin_dir.mkdir(parents=True, exist_ok=True)
        if is_owned_and_writable_dir(admin_dir, uid):
            return admin_dir
    except OSError:
        pass

    for base in fallback_cache_bases:
        fallback = base / f"{FALLBACK_DIR_PREFIX}-{username}-{uid}"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            os.chmod(fallback, CACHE_DIR_MODE)
            if is_owned_and_writable_dir(fallback, uid):
                return fallback
        except OSError:
            continue

    raise PermissionError(
        f"No writable local cache directory available for {username}."
    )


def ensure_cache_symlink(home_dir: Path, local_cache: Path) -> None:
    link_path = home_dir / CACHE_DIR_NAME

    if link_path.is_symlink():
        try:
            resolved = link_path.resolve(strict=True)
            if resolved == local_cache.resolve(strict=True):
                return
        except OSError:
            pass
        link_path.unlink()
    elif link_path.exists() or link_path.is_mount():
        stale = home_dir / f"{STALE_CACHE_PREFIX}.{os.getpid()}.{int(time.time())}"
        link_path.rename(stale)
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale, ignore_errors=True)
        else:
            stale.unlink(missing_ok=True)

    try:
        link_path.symlink_to(local_cache, target_is_directory=True)
    except FileExistsError:
        link_path.unlink(missing_ok=True)
        link_path.symlink_to(local_cache, target_is_directory=True)

    if not link_path.is_symlink() or not link_path.is_dir():
        raise RuntimeError(f"Invalid symlink at {link_path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    home_str = os.environ.get("HOME", "")
    home_dir = Path(home_str) if home_str else Path.home()

    if not str(home_dir).startswith(args.remote_home_prefix):
        return 0

    fallback_bases = (
        tuple(args.fallback_cache_bases)
        if args.fallback_cache_bases
        else DEFAULT_FALLBACK_CACHE_BASES
    )

    username = os.environ.get("USER") or getpass.getuser()
    uid = os.getuid()

    try:
        local_cache = resolve_local_cache(
            username=username,
            uid=uid,
            admin_cache_base=args.admin_cache_base,
            fallback_cache_bases=fallback_bases,
        )
        ensure_cache_symlink(home_dir, local_cache)
    except OSError as exc:
        print(f"setup-user-cache: {exc}", file=sys.stderr)
        return 1

    print(local_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
