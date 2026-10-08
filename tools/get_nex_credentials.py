#!/usr/bin/env python3
"""Extract NEX credentials for 3DS-RPC from a dumped Friends (FRD) account save.

Run the script with no arguments for an interactive guided walkthrough, or pass
paths directly for scriptable use.

The Friends sysmodule (title 00010032, "FRD") stores a small "FPAC" (Friend
Presence Account Config) structure per local account:

    offset  size  field
    0x10    4     Local Account ID
    0x14    4     Principal ID                                -> NINTENDO_PID
    0x18    8     LocalFriendCode
    0x20    32    16-char UTF-16 NEX password (no terminator)  -> NINTENDO_NEX_PASSWORD
    0x40    2     account marker byte (ignored)
    0x42    18    8-char UTF-16 Principal ID HMAC (+ NULL)     -> NINTENDO_PID_HMAC
    0x54    1     NASC environment (0=Production, 1=Testing, 2=Development)

How to obtain the file (GodMode9 - recommended):

  1. Power the console off, then hold (START) while powering it back on to
     launch GodMode9. (GodMode9 must be at SD:/luma/payloads/GodMode9.firm. If
     a chainloader menu appears instead, choose "GodMode9" and press (A).)
  2. Browse to:  [1:] SYSNAND CTRNAND/data/<ID0>/sysdata/00010032
     (<ID0> is a 32-character hex folder unique to your console.)
  3. Select "00000000", press (A), and choose "Copy to 0:/gm9/out".
     No decryption is needed - the Friends save is stored as plaintext FPAC.
  4. Power off with (R)+(START), move the SD card to your PC, and point this
     script at <SD>/gm9/out/00000000.

  Run the script with --how-to godmode9 to print these steps again.

Notes:
  * If nothing is found, the dump is probably still encrypted (the FPAC magic
    will not be present in plaintext).
  * 3DS consoles do not send NEX credentials over NASC. They are generated once
    and stored on the device, so dumping is the only way to read them.
  * The NEX password is NOT your NNID password.
  * A Nimbus-patched console keeps both accounts in the same save: local
    account 1 (NASC "Production") is Nintendo and local account 2 (NASC
    "Testing") is Pretendo. Each account is mapped to the matching
    NINTENDO_*/PRETENDO_* names automatically.
  * Incomplete/duplicate records (for example stale PID=0 copies) are ignored.

This script only prints values. It never modifies any file or talks to any
server. Pretendo HMAC regeneration is handled separately by
tools/rotate_uidhmac.py.
"""

import argparse
import json
import sys
from pathlib import Path

MAGIC = b"FPAC"
MAGIC_NUMBER = 0x20101021

OFFSET_LOCAL_ACCOUNT_ID = 0x10
OFFSET_PRINCIPAL_ID = 0x14
OFFSET_FRIEND_CODE = 0x18
OFFSET_NEX_PASSWORD = 0x20
OFFSET_PID_HMAC = 0x42
OFFSET_NASC_ENV = 0x54
RECORD_SIZE = OFFSET_NASC_ENV + 1

PASSWORD_LENGTH = 16 * 2
HMAC_LENGTH = (8 + 1) * 2

NASC_ENVIRONMENTS = {
    0: "Production",
    1: "Testing",
    2: "Development",
}

# Nimbus sets local account 1 to "prod" (Nintendo) and account 2 to "test"
# (Pretendo). The development environment also belongs to Nintendo.
ENV_NETWORKS = {
    0: "nintendo",
    1: "pretendo",
    2: "nintendo",
}

NETWORK_PREFIXES = {
    "nintendo": "NINTENDO",
    "pretendo": "PRETENDO",
}

NETWORK_LABELS = {
    "nintendo": "Nintendo",
    "pretendo": "Pretendo",
}

BANNER = """\
============================================================
 3DS-RPC - NEX credential helper
============================================================
This reads your console's PID, NEX password and PID HMAC from a
dumped Friends save (system title 00010032, magic "FPAC").

The next step walks you through dumping that file from your
console, then reads the credentials out of it.

Type 'q' at any prompt to quit.
"""

HOWTO = {
    "godmode9": """\
Dumping the Friends save with GodMode9
--------------------------------------
1. Power the console off. Insert the SD card if it is not already in.
2. Hold (START) and power on while holding it.
   - GodMode9 must be installed at SD:/luma/payloads/GodMode9.firm.
   - If a chainloader menu appears instead, choose "GodMode9" and press (A).
3. In GodMode9, browse to:
     [1:] SYSNAND CTRNAND/data/<ID0>/sysdata/00010032
   (<ID0> is a 32-character hex folder unique to your console.)
4. Select the file "00000000" and press (A).
5. Choose "Copy to 0:/gm9/out". No decryption is needed - the Friends
   save is stored as plaintext FPAC data.
6. Hold (R) and press (START) to power off, then take out the SD card.
7. Put the SD card in your PC. The dump is at:  <SD>/gm9/out/00000000
""",
}

METHOD_PROMPT = """\
How will you dump the Friends save from your console?
  1) GodMode9 (recommended)
  2) I already have the file
"""


def decode_utf16(raw: bytes) -> str:
    """Decode a NULL-terminated UTF-16LE field into a clean string."""
    return raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]


def iter_frd_records(data: bytes):
    """Yield every valid FPAC record found in a raw byte buffer."""
    start = 0
    while True:
        index = data.find(MAGIC, start)
        if index < 0:
            return
        start = index + len(MAGIC)

        if index + RECORD_SIZE > len(data):
            continue
        if int.from_bytes(data[index + 4:index + 8], "little") != MAGIC_NUMBER:
            continue

        base = index
        yield {
            "offset": index,
            "local_account_id": int.from_bytes(
                data[base + OFFSET_LOCAL_ACCOUNT_ID:base + OFFSET_LOCAL_ACCOUNT_ID + 4], "little"
            ),
            "pid": int.from_bytes(
                data[base + OFFSET_PRINCIPAL_ID:base + OFFSET_PRINCIPAL_ID + 4], "little"
            ),
            "friend_code": int.from_bytes(
                data[base + OFFSET_FRIEND_CODE:base + OFFSET_FRIEND_CODE + 8], "little"
            ),
            "nex_password": decode_utf16(
                data[base + OFFSET_NEX_PASSWORD:base + OFFSET_NEX_PASSWORD + PASSWORD_LENGTH]
            ),
            "pid_hmac": decode_utf16(
                data[base + OFFSET_PID_HMAC:base + OFFSET_PID_HMAC + HMAC_LENGTH]
            ),
            "nasc_environment": data[base + OFFSET_NASC_ENV],
        }


def collect_inputs(paths):
    """Expand the user-supplied paths into a list of (label, bytes) sources."""
    sources = []

    def add_file(path: Path):
        try:
            data = path.read_bytes()
        except OSError as error:
            print("warning: could not read %s: %s" % (path, error), file=sys.stderr)
            return
        sources.append((str(path), data))

    for raw_path in paths:
        if raw_path == "-":
            sources.append(("<stdin>", sys.stdin.buffer.read()))
            continue

        path = Path(raw_path)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    add_file(child)
        elif path.is_file():
            add_file(path)
        else:
            print("warning: %s does not exist" % path, file=sys.stderr)

    return sources


def gather_records(sources):
    """Turn collected (label, bytes) sources into FPAC record dicts."""
    records = []
    for label, data in sources:
        for record in iter_frd_records(data):
            record["source"] = label
            records.append(record)
    return records


def detect_network(record):
    """Guess which network an FPAC record belongs to from its NASC env."""
    return ENV_NETWORKS.get(record["nasc_environment"], "nintendo")


def is_valid_record(record):
    """A usable record needs a Principal ID and a NASC HMAC."""
    return record["pid"] != 0 and bool(record["pid_hmac"])


def prepare_records(records):
    """Drop incomplete and duplicate records, returning (kept, skipped)."""
    kept = []
    skipped = []
    seen = set()
    for record in records:
        key = (record["local_account_id"], record["pid"], record["pid_hmac"])
        if not is_valid_record(record) or key in seen:
            skipped.append(record)
            continue
        seen.add(key)
        kept.append(record)
    kept.sort(key=lambda item: (item["local_account_id"], item["offset"]))
    return kept, skipped


def select_records(records, selection):
    """Filter records by the user's network choice ('auto' keeps everything)."""
    if selection in (None, "auto", "both"):
        return list(records)
    return [record for record in records if detect_network(record) == selection]


def formatted_lines(record):
    """Build the ready-to-paste api/private.py lines for one record."""
    prefix = NETWORK_PREFIXES[detect_network(record)]
    return [
        "%s_PID:int = %d" % (prefix, record["pid"]),
        "%s_NEX_PASSWORD:str = %s" % (prefix, repr(record["nex_password"])),
        "%s_PID_HMAC:str = %s" % (prefix, repr(record["pid_hmac"])),
    ]


def display_records(records):
    """Print a human-readable summary and the private.py snippet."""
    for number, record in enumerate(records):
        if number:
            print()
        environment = NASC_ENVIRONMENTS.get(
            record["nasc_environment"], "Unknown (%d)" % record["nasc_environment"]
        )
        print("Source           : %s" % record["source"])
        print("  Network        : %s" % NETWORK_LABELS[detect_network(record)])
        print("  FPAC offset    : 0x%X" % record["offset"])
        print("  NASC env       : %s" % environment)
        print("  Local account  : %d" % record["local_account_id"])
        print("  Local friend c : 0x%016X" % record["friend_code"])
        print("  NEX PID        : %d (0x%08X)" % (record["pid"], record["pid"]))
        print("  NEX password   : %s" % record["nex_password"])
        print("  PID HMAC       : %s" % record["pid_hmac"])
        print()
        print("# ---- paste into api/private.py ----")
        for line in formatted_lines(record):
            print(line)


def emit_json(records):
    payload = []
    for record in records:
        payload.append({
            "source": record["source"],
            "network": detect_network(record),
            "offset": record["offset"],
            "local_account_id": record["local_account_id"],
            "pid": record["pid"],
            "friend_code": record["friend_code"],
            "nex_password": record["nex_password"],
            "pid_hmac": record["pid_hmac"],
            "nasc_environment": NASC_ENVIRONMENTS.get(
                record["nasc_environment"], record["nasc_environment"]
            ),
        })
    print(json.dumps(payload, indent=2))


def _ask(prompt):
    """input() that returns None on EOF / Ctrl-C instead of raising."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def clean_path(raw):
    """Trim whitespace and the quotes drag-and-drop tends to add on Windows."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    return raw


def ask_network(default):
    default_number = {"auto": "1", "nintendo": "2", "pretendo": "3"}.get(default, "1")
    print("Which credentials do you want?")
    print("  1) Auto-detect from the save (recommended)")
    print("  2) Nintendo only (prod account)")
    print("  3) Pretendo only (test account)")
    while True:
        choice = _ask("Choice [%s]: " % default_number)
        if choice is None:
            return None
        choice = choice.strip().lower()
        if not choice or choice in ("1", "auto", "a", "both", "b"):
            return "auto"
        if choice in ("2", "nintendo", "n"):
            return "nintendo"
        if choice in ("3", "pretendo", "p"):
            return "pretendo"
        print("Please enter 1, 2 or 3.")


def ask_method():
    print()
    print(METHOD_PROMPT.rstrip())
    while True:
        choice = _ask("Choice [1]: ")
        if choice is None:
            return None
        choice = choice.strip().lower()
        if not choice or choice in ("1", "godmode9", "g"):
            return "godmode9"
        if choice in ("2", "file"):
            return "file"
        print("Please enter 1 or 2.")


def guided_mode(default_network="auto"):
    print(BANNER)
    network = default_network

    method = ask_method()
    if method is None:
        return 0
    if method in HOWTO:
        print()
        print(HOWTO[method].rstrip())
        print()

    while True:
        raw = _ask("Path to the Friends save file or folder (or 'q' to quit): ")
        if raw is None:
            return 0
        raw = clean_path(raw)
        if raw.lower() in ("q", "quit", "exit"):
            return 0
        if not raw:
            continue
        if not Path(raw).exists():
            print("! That path does not exist. Try again.")
            continue

        records = gather_records(collect_inputs([raw]))
        if not records:
            print("! No FPAC records found there.")
            print("  Make sure you exported the Friends (00010032) save DECRYPTED and")
            print("  point at the exported folder, not the SD card root.")
            continue

        valid, skipped = prepare_records(records)
        if not valid:
            print("! Found FPAC records, but none had a usable PID/HMAC.")
            continue
        if skipped:
            print("(ignored %d incomplete or duplicate record(s))" % len(skipped))
        print("\nFound %d usable account(s)." % len(valid))

        chosen = ask_network(network)
        if chosen is None:
            return 0
        network = chosen

        selected = select_records(valid, network)
        if not selected:
            available = ", ".join(sorted({NETWORK_LABELS[detect_network(r)] for r in valid}))
            print("! No %s account in this save (it has: %s)."
                  % (NETWORK_LABELS.get(network, network), available))
        else:
            print()
            display_records(selected)

        again = _ask("\nCheck another save? [y/N]: ")
        if again is None:
            return 0
        if again.strip().lower() not in ("y", "yes"):
            print("\nDone. Copy the lines above into api/private.py.")
            return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Extract 3DS NEX credentials from a dumped Friends (FRD) account save. "
            "Run with no arguments for a guided walkthrough."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/get_nex_credentials.py\n"
            "  python tools/get_nex_credentials.py --how-to godmode9\n"
            "  python tools/get_nex_credentials.py 00010032/\n"
            "  python tools/get_nex_credentials.py account --network pretendo\n"
            "  python tools/get_nex_credentials.py account --network both --json"
        ),
    )
    parser.add_argument("paths", nargs="*", help="save file(s), a folder, or - for stdin")
    parser.add_argument(
        "--network",
        choices=["auto", "nintendo", "pretendo", "both"],
        default=None,
        help="which account(s) to emit: auto maps each account by NASC env (default)",
    )
    parser.add_argument("--json", action="store_true", help="print records as JSON instead")
    parser.add_argument(
        "--guided", action="store_true", help="force the interactive guided walkthrough"
    )
    parser.add_argument(
        "--how-to",
        choices=["godmode9"],
        help="print dumping instructions and exit",
    )
    args = parser.parse_args(argv)

    if args.how_to:
        print(HOWTO[args.how_to].rstrip())
        return 0

    if args.guided or (not args.paths and not args.json):
        return guided_mode(args.network or "auto")

    if args.json and not args.paths:
        parser.error("--json requires at least one path to read")

    records = gather_records(collect_inputs(args.paths))

    if not records:
        print(
            "No FPAC records found.\n"
            "Make sure you exported the Friends (00010032) save decrypted. "
            "If it is encrypted, the FPAC magic will not appear in the file.",
            file=sys.stderr,
        )
        return 1

    valid, skipped = prepare_records(records)
    if skipped:
        print("Ignored %d incomplete or duplicate record(s)." % len(skipped), file=sys.stderr)
    if not valid:
        print("No usable account records (missing PID/HMAC).", file=sys.stderr)
        return 1

    selected = select_records(valid, args.network or "auto")
    if not selected:
        available = ", ".join(sorted({NETWORK_LABELS[detect_network(r)] for r in valid}))
        print("No matching account. This dump has: %s" % available, file=sys.stderr)
        return 1

    if args.json:
        emit_json(selected)
        return 0

    display_records(selected)
    return 0


if __name__ == "__main__":
    sys.exit(main())
