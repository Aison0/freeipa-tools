#!/usr/bin/env python3
"""
pull-ipa-local-groups.py
------------------------
Fetches the central configuration file for sync-local-groups.py over HTTPS,
validates its syntax, and atomically updates /etc/security/ipa-local-groups.conf.

How it works:
  - Downloads the configuration file from a given URL (via --url or CONFIG_URL).
  - Supports optional HTTP Authorization headers (via --auth-header or AUTH_HEADER)
    and custom CA bundles (via --ca-bundle or CA_BUNDLE).
  - Validates that the fetched file is non-empty and contains valid rules in the
    format: <user> <ipa-group> <local-groups>
  - Compares the fetched file with the existing local configuration and exits
    early if nothing has changed.
  - Atomically replaces /etc/security/ipa-local-groups.conf with 0644 (root:root)
    permissions and logs updates to syslog (LOG_INFO).

Usage:
  - systemd oneshot service : /usr/local/bin/pull-ipa-local-groups.py
  - Manual CLI / Dry-Run    : pull-ipa-local-groups.py --url https://... --dry-run
"""

import argparse
import os
import ssl
import sys
import syslog
import tempfile
import urllib.error
import urllib.request

DEFAULT_CONFIG_PATH = "/etc/security/ipa-local-groups.conf"
DEFAULT_TIMEOUT = 20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and validate central ipa-local-groups.conf over HTTPS."
    )
    parser.add_argument(
        "-u", "--url",
        default=os.environ.get("CONFIG_URL", "").strip(),
        help="HTTPS URL of the central ipa-local-groups.conf (or set CONFIG_URL)",
    )
    parser.add_argument(
        "-o", "--output",
        default=os.environ.get("DST_CONF", DEFAULT_CONFIG_PATH).strip(),
        help=f"Destination config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-H", "--auth-header",
        default=os.environ.get("AUTH_HEADER", "").strip(),
        help="Optional HTTP Authorization header value (e.g. 'Bearer <token>')",
    )
    parser.add_argument(
        "--ca-bundle",
        default=os.environ.get("CA_BUNDLE", "").strip(),
        help="Optional path to custom CA certificate bundle",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=int(os.environ.get("PULL_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Download and validate configuration without writing to disk",
    )
    return parser.parse_args()


def validate_config_content(raw_bytes: bytes) -> None:
    """Ensure the downloaded content is valid UTF-8, non-empty, and has valid rule lines."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Configuration is not valid UTF-8: {exc}") from exc

    valid_rules = 0
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError(
                f"Invalid rule format on line {line_no}: expected '<user> <ipa-group> <local-groups>'"
            )
        valid_rules += 1

    if valid_rules == 0:
        raise ValueError("Configuration contains no active rules.")


def fetch_config(url: str, auth_header: str, ca_bundle: str, timeout: int) -> bytes:
    if not url:
        raise ValueError("No configuration URL provided (use --url or CONFIG_URL).")
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"Unsupported URL scheme in '{url}'.")

    headers = {"User-Agent": "pull-ipa-local-groups/1.0"}
    if auth_header:
        headers["Authorization"] = auth_header

    req = urllib.request.Request(url, headers=headers, method="GET")

    ssl_context = None
    if url.startswith("https://"):
        if ca_bundle:
            ssl_context = ssl.create_default_context(cafile=ca_bundle)
        else:
            ssl_context = ssl.create_default_context()

    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Unexpected HTTP status {resp.status} from {url}")
        return resp.read()


def atomic_write(dst_path: str, data: bytes) -> bool:
    """
    Write data atomically to dst_path if content differs.
    Returns True if the file was updated, False if unchanged.
    """
    if os.path.isfile(dst_path):
        with open(dst_path, "rb") as f:
            if f.read() == data:
                return False

    dst_dir = os.path.dirname(os.path.abspath(dst_path))
    os.makedirs(dst_dir, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".ipa-local-groups.conf.", dir=dst_dir)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        os.chmod(tmp_path, 0o644)
        if os.geteuid() == 0:
            os.chown(tmp_path, 0, 0)

        os.replace(tmp_path, dst_path)
        return True
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def main() -> int:
    args = parse_args()

    try:
        data = fetch_config(
            url=args.url,
            auth_header=args.auth_header,
            ca_bundle=args.ca_bundle,
            timeout=args.timeout,
        )
        validate_config_content(data)
    except (ValueError, RuntimeError, urllib.error.URLError, ssl.SSLError, OSError) as exc:
        msg = f"pull-ipa-local-groups: failed to update {args.output}: {exc}"
        syslog.syslog(syslog.LOG_ERR, msg)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"Dry-run: successfully fetched and validated configuration from {args.url} ({len(data)} bytes).")
        return 0

    try:
        updated = atomic_write(args.output, data)
    except OSError as exc:
        msg = f"pull-ipa-local-groups: failed writing {args.output}: {exc}"
        syslog.syslog(syslog.LOG_ERR, msg)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if updated:
        msg = f"pull-ipa-local-groups: updated {args.output} from {args.url}"
        syslog.syslog(syslog.LOG_INFO, msg)
        print(msg)
    else:
        print(f"No changes for {args.output}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
