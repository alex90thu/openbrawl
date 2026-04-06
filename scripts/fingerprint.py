#!/usr/bin/env python3
"""Generate a stable OpenClaw fingerprint for x-openclaw-fingerprint header.

Recipe:
sha256(machine_id + "|" + username + "|" + install_path)
"""

import getpass
import hashlib
import os
import platform
import sys
from pathlib import Path


DEFAULT_INSTALL_PATH = str(Path.cwd().resolve())


def read_machine_id() -> str:
    # Linux primary source
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
            if value:
                return value
        except Exception:
            pass

    # Fallback for non-Linux or restricted environments
    node = hex(hash(platform.node()))[2:]
    system = platform.system().lower()
    release = platform.release().lower()
    return f"fallback-{system}-{release}-{node}"


def build_fingerprint(username: str, install_path: str) -> str:
    machine_id = read_machine_id()
    normalized_path = str(Path(install_path).expanduser().resolve())
    seed = f"{machine_id}|{username}|{normalized_path}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def main() -> int:
    username = os.environ.get("OPENCLAW_USER", getpass.getuser()).strip()
    install_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INSTALL_PATH

    if not username:
        print("ERROR: username is empty")
        return 1

    fingerprint = build_fingerprint(username, install_path)
    print(fingerprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
