#!/usr/bin/env python3
"""One-time, NON-DESTRUCTIVE migration of a mailbox's pre-fix Sieve scripts.

Run on the Docker host with the native_vmail volume MOUNTPOINT as --vmail-root
and the mailbox's exact Dovecot userdb home directory as --mail-home.

Always dry-run first; --apply copies (never moves/removes) the old scripts and
sets a matching new active symlink OUTSIDE every Maildir. Do not retire old
files until the new Dovecot config and an actual delivery have been verified.
The entire vmail volume must be backed up beforehand.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import sys

def migrate(volume: Path, home: Path, address: str, apply: bool) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+", address):
        raise ValueError("Supply a valid mailbox address")
    local, domain = address.lower().rsplit("@", 1)
    root = volume.resolve(strict=True)
    if home.is_symlink():
        raise ValueError("Refusing a symlinked mailbox home")
    mailbox_home = home.resolve(strict=True)
    if root not in mailbox_home.parents or mailbox_home == root:
        raise ValueError("Mailbox home must belong to the existing vmail volume")
    if mailbox_home.is_symlink():
        raise ValueError("Refusing a symlinked mailbox home")
    scripts = mailbox_home / "sieve"
    active = mailbox_home / ".dovecot.sieve"
    if not scripts.is_dir() or not active.is_symlink():
        raise ValueError("Expected original sieve directory and active symlink")
    current = (mailbox_home / os.readlink(active)).resolve(strict=True)
    if scripts.resolve(strict=True) not in current.parents or current.suffix != ".sieve":
        raise ValueError("Active script must point to a file inside original sieve directory")
    files = sorted(scripts.glob("*.sieve"))
    if not files or current not in [p.resolve() for p in files]:
        raise ValueError("The original active script was not found")
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError("Refusing symlinked or non-regular scripts")
    destination = root / ".sieve" / domain / local
    target = destination / "scripts"
    link = destination / "active.sieve"
    if destination.exists():
        # This operation must never silently replace an active production rule.
        raise ValueError("Destination already exists; inspect manually before retrying")
    if not apply:
        return f"DRY_RUN: {len(files)} script(s) can be copied; old originals preserved"
    owner = mailbox_home.stat()
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        parents = [root / ".sieve", root / ".sieve" / domain, destination, target]
        for p in parents:
            if os.geteuid() == 0:
                os.chown(p, owner.st_uid, owner.st_gid)
            p.chmod(0o700)
        for p in files:
            out = target / p.name
            shutil.copy2(p, out, follow_symlinks=False)
            if os.geteuid() == 0:
                os.chown(out, owner.st_uid, owner.st_gid)
            out.chmod(0o600)
        link.symlink_to(Path("scripts") / current.name)
        if not link.resolve(strict=True).is_file():
            raise ValueError("Migrated active script link is invalid")
    except Exception:
        # Do not delete failed target automatically; preserve files for forensics.
        raise
    return f"COPIED: {len(files)} script(s); original scripts and active link untouched"

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vmail-root", type=Path, required=True)
    parser.add_argument("--mail-home", type=Path, required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(migrate(args.vmail_root, args.mail_home, args.address, args.apply))
    except (ValueError, OSError) as exc:
        print("STOP:", exc, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
