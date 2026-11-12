"""Create a demo keyring file without printing key material to the terminal."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
from contextlib import suppress
from pathlib import Path

KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an EVIDENCE demo keyring file.")
    parser.add_argument(
        "--output", default="secrets/evidence-master-keys.json",
        help="New file to create; an existing file is never overwritten.",
    )
    parser.add_argument("--version", default="v1", help="Initial key version, default: v1")
    args = parser.parse_args()

    if not KEY_VERSION_PATTERN.fullmatch(args.version):
        parser.error("version must contain only letters, digits, dot, underscore, or hyphen (1-32 chars)")

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    payload = json.dumps({args.version: key}, indent=2) + "\n"
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error(f"refusing to overwrite existing keyring: {output}")
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    with suppress(OSError):
        output.chmod(0o600)
    print(f"Created keyring: {output}")


if __name__ == "__main__":
    main()
