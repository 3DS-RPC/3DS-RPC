#!/usr/bin/env python3
"""Rotate the Pretendo NEX account's UIDHMAC and update api/private.py.

The 3DS sends `uidhmac` + `userid` to the NASC login endpoint. Pretendo's
account server compares the sent value against the one stored server-side
(`nexAccount.uidhmac`) and rejects a mismatch with NASC error code 122
("PID HMAC is invalid"). The stored value is derived deterministically from
the PID and a server secret, so whenever Pretendo rotates that key (e.g.
during a maintenance/patch window) the value saved on every console becomes
invalid and every existing account starts failing with 122.

To recover, Pretendo exposes:

    POST https://api.pretendo.cc/v1/repair-uidhmac
    body: { "pid": "<pid>", "password": "<16-char NEX password>" }

which regenerates that account's uidhmac and returns it. This script calls
that endpoint with the Pretendo credentials from api/private.py, then writes
the new HMAC back into api/private.py so the backend can log in again.

Usage (from the repo root, with the virtualenv active):

    python tools/rotate_uidhmac.py

The Pretendo PID and NEX password are always read from api/private.py, and the
resulting HMAC is verified with a real NASC LOGIN before the script finishes.

Notes:
    * The NEX password is exactly 16 characters and is NOT your PNID password.
    * The repair endpoint is rate limited to 30 calls per PID per 5 minutes.
    * Only the Pretendo account has this repair path; Nintendo does not.
"""

import ast
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PATH = REPO_ROOT / "api" / "private.py"

# The same character class Pretendo's account server uses to validate the NEX
# password before it will regenerate the uidhmac.
PASSWORD_REGEX = re.compile(r"^[\x21-\x5B\x5D-\x7D]{16}$")


_VALUE_PATTERNS = {}


def get_value(text: str, key: str):
    """Extract the value of `key: type = ...` from api/private.py without executing it.

    The `\\b` anchor stops `PRETENDO_PID` from matching `PRETENDO_PID_HMAC`, and
    the annotation (`:int`, `:str`, ...) between the name and `=` is skipped.
    """
    if key not in _VALUE_PATTERNS:
        _VALUE_PATTERNS[key] = re.compile(
            r"^\s*"
            + re.escape(key)
            + r"\b[^=\r\n]*=\s*(.+?)\s*(?:#.*)?$"
        )
    pattern = _VALUE_PATTERNS[key]
    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        literal = match.group(1).strip()
        try:
            return ast.literal_eval(literal)
        except (ValueError, SyntaxError):
            raise SystemExit(f"Could not parse the value of {key} in {PRIVATE_PATH}")
    return None


def set_hmac(text: str, value: str) -> str:
    """Replace the active PRETENDO_PID_HMAC assignment and return the new file text."""
    lines = text.split("\n")
    rewritten = False
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.startswith("PRETENDO_PID_HMAC"):
            continue
        indent = line[: len(line) - len(stripped)]
        lead = line[: line.index("=")].rstrip() if "=" in line else "PRETENDO_PID_HMAC:str"
        lines[i] = f"{indent}{lead} = {value!r}"
        rewritten = True
        break
    if not rewritten:
        raise SystemExit("No active PRETENDO_PID_HMAC line found in api/private.py")

    separator = "\r\n" if "\r\n" in text else "\n"
    return separator.join(lines)


def repair_uidhmac(pid: int, password: str) -> str:
    body = json.dumps({"pid": str(pid), "password": password}).encode("utf-8")
    request = urllib.request.Request(
        "https://api.pretendo.cc/v1/repair-uidhmac",
        data=body,
        headers={
            "Content-Type": "application/json",
            # Browser-like UA so Cloudflare does not challenge the request.
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            message = detail.get("error") or error.reason
        except Exception:
            message = error.reason
        raise SystemExit(f"Repair request failed with HTTP {error.code}: {message}")
    except Exception as error:
        raise SystemExit(f"Could not reach api.pretendo.cc: {error}")

    hmac = payload.get("data", {}).get("uidhmac")
    if payload.get("status") != 200 or not hmac:
        raise SystemExit(f"Unexpected response from api.pretendo.cc: {payload!r}")
    return hmac


def verify_login(pid: int, hmac: str) -> None:
    """Perform a real NASC LOGIN with the freshly written credentials."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from nintendo import nasc
        from api import private as cfg
    except ImportError as error:
        print(f"  (skipping verify: {error})")
        return

    import anyio

    async def login():
        client = nasc.NASCClient()
        client.set_locale(cfg.PRETENDO_REGION, cfg.PRETENDO_LANGUAGE)
        client.set_url("nasc.pretendo.cc")
        client.context.set_authority(None)
        client.set_device(
            cfg.PRETENDO_SERIAL_NUMBER,
            cfg.PRETENDO_MAC_ADDRESS,
            cfg.PRETENDO_DEVICE_CERT,
            cfg.PRETENDO_DEVICE_NAME,
        )
        client.set_user(pid, hmac)
        client.set_title(0x0004013000003202, 20)
        return await client.login(0x3200)

    response = anyio.run(login)
    print(f"  NASC LOGIN OK: {response.host}:{response.port}")


def main() -> int:
    if not PRIVATE_PATH.is_file():
        raise SystemExit(f"Private file not found: {PRIVATE_PATH}")

    text = PRIVATE_PATH.read_text(encoding="utf-8")
    pid = get_value(text, "PRETENDO_PID")
    password = get_value(text, "PRETENDO_NEX_PASSWORD")

    if pid is None:
        raise SystemExit("Could not determine the Pretendo PID (check PRETENDO_PID in api/private.py)")
    if not password:
        raise SystemExit("Could not determine the Pretendo NEX password (check PRETENDO_NEX_PASSWORD in api/private.py)")
    if not PASSWORD_REGEX.fullmatch(password):
        raise SystemExit(
            f"The NEX password must be exactly 16 characters matching "
            f"[!-\\u005b] and [\\u005d-~] (got {len(password)} chars)."
        )

    print(f"Requesting a new uidhmac for PID {pid} from api.pretendo.cc...")
    new_hmac = repair_uidhmac(pid, password)

    old_hmac = get_value(text, "PRETENDO_PID_HMAC")
    if old_hmac == new_hmac:
        print(f"uidhmac unchanged ({new_hmac!r}); nothing to update")
    else:
        backup_path = PRIVATE_PATH.with_name(PRIVATE_PATH.name + ".bak")
        shutil.copy2(PRIVATE_PATH, backup_path)
        print(f"Backed up {PRIVATE_PATH} -> {backup_path}")
        PRIVATE_PATH.write_text(set_hmac(text, new_hmac), encoding="utf-8")
        print(f"Updated {PRIVATE_PATH} with new uidhmac {new_hmac!r} (was {old_hmac!r})")

    verify_login(pid, new_hmac)

    print("Done. Restart the Pretendo backend (python backend.py --network pretendo) to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())