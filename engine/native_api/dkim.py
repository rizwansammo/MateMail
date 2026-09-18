"""
DKIM key lifecycle — the reason this service exists at all.

DEC-007r
    The private key is generated inside the engine, lives on the engine
    filesystem at mode 0600, and NEVER crosses `MailEngineAdapter`. It is not in
    the database, not in an API response, not in a log line, and not in
    MateMail. `DkimKeyInfo` has no field it could travel in.

WHERE THE KEY LIVES
    `/var/lib/rspamd/dkim/<domain>.<selector>.key` is the ACTIVE key — exactly
    the path `rspamd/local.d/dkim_signing.conf` interpolates:

        path = "/var/lib/rspamd/dkim/$domain.$selector.key"

    Alongside it sit immutable generation files:

        /var/lib/rspamd/dkim/<domain>.<selector>.g<token>.key

    A generation file is written once and never modified. Activation makes the
    fixed path a HARD LINK to one of them, so "which generation is live" is an
    inode identity rather than a claim someone recorded.

ONE SOURCE OF TRUTH, SO A MISMATCH IS UNREPRESENTABLE
    An earlier NE2 draft committed the database row and then moved the key into
    place. Those are two commits, so a crash between them left the row naming
    one generation while Rspamd signed with another — a published DNS record
    that does not verify the mail it covers.

    No ordering of two commits fixes that. What fixes it is having one artifact:

        the active private key file IS the authority

    `public_material_of()` derives the public key from whatever is actually at
    the active path, and `get_dkim_public_key` returns THAT. Whatever Rspamd
    signs with is, by construction, what MateMail publishes. The database row is
    metadata and a cache; when it disagrees it is the row that is wrong, and
    reconciliation corrects it.

    So after a crash at ANY step:

        generated, not activated   -> active path unchanged; the OLD generation
                                      is fully usable; the orphan is reconciled
        activated, row not updated -> the NEW generation is fully usable and the
                                      derived public key matches it; the row
                                      self-heals on the next read or reconcile
        row updated, not activated -> cannot happen: the row is written AFTER
                                      activation, and nothing reads it directly

WHY HARD LINKS RATHER THAN A SYMLINK
    A symlink can dangle; a hard link cannot. Both names refer to one inode with
    mode 0600, so the fixed path is a real key file rather than a pointer, and
    `os.replace` over it is atomic. Removing the generation name later does not
    remove the key while the fixed path still references the inode.
"""
from __future__ import annotations

import base64
import contextlib
import logging
import os
import pathlib
import re
import secrets
import tempfile
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

logger = logging.getLogger("matemail.native.api.dkim")

#: 2048 matches DEFAULT_DKIM_KEY_SIZE in MateMail's DTO layer, and for the same
#: reason recorded there: 1024 is deprecated and increasingly distrusted, while
#: 4096 produces a TXT record that must be split across strings and is
#: mishandled by some DNS providers customers actually use.
DEFAULT_KEY_SIZE = 2048

#: Below this the key is not worth generating. A caller asking for 1024 has made
#: a mistake the engine should refuse rather than quietly honour.
MINIMUM_KEY_SIZE = 2048

KEY_DIR = pathlib.Path(os.environ.get("NATIVE_DKIM_DIR", "/var/lib/rspamd/dkim"))

#: Owner read/write only. The API runs as Rspamd's uid precisely so this can
#: stay 0600 and still be readable by the signer at NE3.
_PRIVATE_MODE = 0o600
_DIR_MODE = 0o700

#: `<domain>.<selector>.g<token>.key`
#: Rspamd reads this for the per-domain selector. Not key material: it maps a
#: domain to a selector, both of which are public in DNS.
SELECTOR_MAP_NAME = "selectors.map"

_GENERATION_RE = re.compile(r"^(?P<domain>.+)\.(?P<selector>[^.]+)\.g(?P<token>[0-9a-f]{16})\.key$")


class DkimError(RuntimeError):
    """Key material could not be produced or stored."""


# ── paths ───────────────────────────────────────────────────────────────────


def key_path(domain: str, selector: str) -> pathlib.Path:
    """The ACTIVE key: what Rspamd reads."""
    return KEY_DIR / f"{domain}.{selector}.key"


def generation_path(domain: str, selector: str, token: str) -> pathlib.Path:
    """One immutable generation. Written once, never modified."""
    return KEY_DIR / f"{domain}.{selector}.g{token}.key"


def parse_generation(name: str) -> dict | None:
    match = _GENERATION_RE.match(name)
    return match.groupdict() if match else None


# ── the selector map Rspamd reads ───────────────────────────────────────────


def selector_map_path() -> pathlib.Path:
    return KEY_DIR / SELECTOR_MAP_NAME


def write_selector_map(pairs: list[tuple[str, str]]) -> str:
    """
    Publish `<domain> <selector>` for every domain that has a DKIM key.

    WHY THIS FILE EXISTS
        Rspamd has to know which selector signs which domain, and it cannot ask
        PostgreSQL. The alternative shipped in NE1 was a single hardcoded
        `selector = "mm1"` in dkim_signing.conf, which is wrong the moment one
        domain rotates onto a different selector: every message for that domain
        would be signed with a key whose name does not match the DNS record, and
        the signature would fail at the receiver. A broken signature is worse
        than none — it reads as a forgery rather than as unsigned mail.

    WHY THE API WRITES IT
        The engine API is the only component permitted to write the key store
        (NE0.2, DEC-007r), and this file is part of that store: it is derived
        from the same `dkim_key` rows, and it must change in step with them.
        Rspamd mounts the directory read-only and only ever reads it.

    ATOMIC, like the keys themselves. Rspamd re-reads this file on a timer, so a
    partially-written map would be observable — it would mean signing some
    domains with a truncated selector until the next write. The temp-and-rename
    makes every read see either the old map or the new one.
    """
    _ensure_key_dir()
    target = selector_map_path()
    body = "".join(f"{domain} {selector}\n" for domain, selector in sorted(pairs))
    handle, temp_name = tempfile.mkstemp(dir=str(KEY_DIR), prefix=".selectors.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="ascii") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        # World-readable is deliberate and safe: this maps a domain to a
        # selector, both of which are published in DNS. It is not key material.
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    _fsync_dir()
    logger.info("published DKIM selector map with %d domain(s)", len(pairs))
    return str(target)


def _ensure_key_dir() -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)


def _fsync_dir() -> None:
    """Make a rename durable. Without this a crash can lose the directory entry."""
    try:
        fd = os.open(str(KEY_DIR), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:                       # pragma: no cover - platform dependent
        logger.debug("could not fsync the DKIM directory: %s", exc)


# ── public material ─────────────────────────────────────────────────────────


def _public_from_private(private_key) -> str:
    """The `p=` value: base64 of the DER SubjectPublicKeyInfo, no PEM armour."""
    der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


def public_material_of(path: str | os.PathLike) -> str | None:
    """
    Derive the public key from a private key file on disk.

    THE function that makes a DB/filesystem mismatch impossible: the answer
    always comes from the key that will actually sign. Returns None when the
    file is missing or unreadable, which is an honest "this domain has no usable
    key" rather than a stale claim that it does.
    """
    try:
        data = pathlib.Path(path).read_bytes()
    except OSError:
        return None
    try:
        private_key = serialization.load_pem_private_key(data, password=None)
    except (ValueError, TypeError) as exc:
        logger.error("DKIM key at %s is unreadable: %s", path, type(exc).__name__)
        return None
    finally:
        del data
    return _public_from_private(private_key)


def dns_record(domain: str, selector: str, public_key: str) -> tuple[str, str]:
    """The record name and value MateMail should publish."""
    return f"{selector}._domainkey.{domain}", f"v=DKIM1; k=rsa; p={public_key}"


# ── generation and activation ───────────────────────────────────────────────


def generate(domain: str, selector: str, key_size: int = DEFAULT_KEY_SIZE) -> dict:
    """
    Mint a keypair into a new IMMUTABLE generation file.

    Does not touch the active path. Nothing is live until `activate` is called,
    so a crash here leaves the previous generation signing exactly as before and
    the orphan is cleaned up by reconciliation.
    """
    if key_size < MINIMUM_KEY_SIZE:
        raise DkimError(
            f"refusing to generate a {key_size}-bit DKIM key; "
            f"{MINIMUM_KEY_SIZE} is the minimum"
        )

    _ensure_key_dir()
    token = secrets.token_hex(8)
    target = generation_path(domain, selector, token)

    # `cryptography` uses the OS CSPRNG through OpenSSL. Nothing here reaches for
    # `random`.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Temp file in the SAME directory: os.replace is only atomic within a
    # filesystem. `mkstemp` creates it 0600, so the key is never briefly
    # readable by anyone else.
    handle, temp_name = tempfile.mkstemp(dir=str(KEY_DIR), prefix=f".{domain}.", suffix=".tmp")
    try:
        # The descriptor goes straight to fdopen and is closed by the `with`, so
        # the failure path can always unlink. Anything that can raise while the
        # raw fd is open leaks it.
        with os.fdopen(handle, "wb") as fh:
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), _PRIVATE_MODE)
            fh.write(pem)
            fh.flush()
            os.fsync(fh.fileno())
        if not hasattr(os, "fchmod"):
            # Windows: developer machines only. POSIX mode bits are not
            # meaningful there, so the permission assertion is skipped in tests
            # and checked on Linux, which is where it means something.
            try:
                os.chmod(temp_name, _PRIVATE_MODE)
            except OSError:
                pass
        os.replace(temp_name, target)
        _fsync_dir()
    except BaseException:
        # Never leave a private key under a temp name, including on
        # KeyboardInterrupt — hence BaseException.
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    finally:
        del pem

    public_key = _public_from_private(private_key)
    name, value = dns_record(domain, selector, public_key)
    logger.info("generated DKIM generation %s for %s selector %s (%d bits)",
                token, domain, selector, key_size)
    return {
        "selector": selector,
        "public_key": public_key,
        "dns_record_name": name,
        "dns_record_value": value,
        "generation_token": token,
        "generation_path": str(target),
        "active_path": str(key_path(domain, selector)),
        "key_size": key_size,
    }


def activate(generation_file: str | os.PathLike, active_file: str | os.PathLike) -> None:
    """
    Make a generation the live key. Atomic, and the ONLY commit point.

    A hard link into a temporary name, then `os.replace` over the active path.
    Readers see either the previous key or this one, never a partial file and
    never a missing one.
    """
    generation_file = pathlib.Path(generation_file)
    active_file = pathlib.Path(active_file)
    if not generation_file.is_file():
        raise DkimError(f"cannot activate a generation that does not exist: {generation_file}")

    staging = active_file.with_name(f".{active_file.name}.activate-{secrets.token_hex(4)}")
    try:
        os.link(generation_file, staging)
        os.replace(staging, active_file)
        _fsync_dir()
    except BaseException:
        try:
            os.unlink(staging)
        except OSError:
            pass
        raise


def active_generation_token(domain: str, selector: str) -> str | None:
    """
    Which generation is live, by inode identity rather than by record.

    Returns None when nothing is active, or when the active file is not linked
    to any generation file (which reconciliation repairs).
    """
    active = key_path(domain, selector)
    try:
        active_ino = os.stat(active).st_ino
    except OSError:
        return None
    for candidate in KEY_DIR.glob(f"{domain}.{selector}.g*.key"):
        try:
            if os.stat(candidate).st_ino == active_ino:
                parsed = parse_generation(candidate.name)
                return parsed["token"] if parsed else None
        except OSError:
            continue
    return None


def generations_for(domain: str, selector: str) -> list[pathlib.Path]:
    return sorted(KEY_DIR.glob(f"{domain}.{selector}.g*.key"))


def remove(path: str | os.PathLike) -> bool:
    """
    Delete a key file. Idempotent: already gone is the desired state.

    Two callers can legitimately race to remove the same orphan — a losing
    concurrent provisioner cleaning up its own generation while the winner
    prunes the same file. POSIX reports that as FileNotFoundError; Windows
    reports PermissionError instead, so the check is "is it gone?" rather than
    "which errno did we get?".
    """
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False
    except PermissionError:
        if not os.path.exists(path):
            return False
        raise DkimError(f"could not remove DKIM key file: {path}")
    except OSError as exc:
        raise DkimError(f"could not remove DKIM key file: {exc}") from exc


def private_key_mode(path: str | os.PathLike) -> int | None:
    """Permission bits of a stored key, for tests and operator checks."""
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return None


def prune_generations(domain: str, selector: str, keep_token: str | None) -> list[str]:
    """
    Remove generation files that are not the live one.

    Called after a successful activation and by reconciliation. Removing a
    generation NAME never destroys the live key even if the inode is shared —
    the active path still references it — which is the property that makes this
    safe to run at any time.
    """
    removed = []
    for candidate in generations_for(domain, selector):
        parsed = parse_generation(candidate.name)
        if parsed and parsed["token"] == keep_token:
            continue
        # Best effort. Pruning is housekeeping, and failing to delete an orphan
        # must never fail the provisioning call that triggered it — another
        # caller may be removing the same file right now, and reconciliation
        # sweeps up anything left.
        try:
            if remove(candidate):
                removed.append(candidate.name)
        except DkimError as exc:
            logger.warning("could not prune %s: %s", candidate.name, exc)
    return removed


# ── retiring a selector's key instead of deleting it ────────────────────────
#
# THE PROBLEM THIS SOLVES
#     Rspamd resolves a domain's selector from `selectors.map`, which it re-reads
#     on its own watch cycle — `map_watch_interval` defaults to 300 seconds and
#     is an upper bound, not a promise. A selector change that deleted the old
#     key the instant the map was rewritten therefore opened a window: a worker
#     still holding the previous map would look for a key that no longer exists
#     and send the message UNSIGNED.
#
#     That was measured, not theorised — the rotation was observed propagating in
#     about 13 seconds, which is a probability, not an invariant.
#
# THE MODEL
#     A selector change RETIRES the old key rather than deleting it. The file
#     keeps its exact name, because that is the name a stale Rspamd worker will
#     open, and a sidecar marker records when retirement happened.
#
#         <domain>.<selector>.key           still signs, for stale workers
#         <domain>.<selector>.key.retired   epoch seconds, written once
#
#     During the grace period both outcomes are correct signatures:
#
#         stale worker -> old selector + old key -> verifies against the old record
#         fresh worker -> new selector + new key -> verifies against the new record
#
#     Neither is unsigned, and neither is signed with a key the published record
#     does not name.
#
# WHY A SIDECAR FILE AND NOT A DATABASE COLUMN
#     Crash consistency. The marker is written BEFORE the new key is activated,
#     so any crash leaves the retirement recorded next to the key it describes,
#     in the same directory, under the same ownership. A database row could be
#     lost in a crash window and reconciliation would then treat a key that is
#     still in use as an orphan and delete it early — precisely the failure this
#     whole mechanism exists to prevent.
#
#     The marker is metadata about a private key, never key material, and it is
#     never served through the API.

#: Sidecar suffix. Deliberately does NOT end in `.key`, so `glob("*.key")` in the
#: reconciliation sweep cannot mistake a marker for a key.
RETIREMENT_SUFFIX = ".retired"

#: How long a retired key keeps signing. Must comfortably exceed Rspamd's
#: `map_watch_interval` (300s by default) plus process scheduling slack; 30
#: minutes is six times that and still short enough that retired material does
#: not accumulate. Overridable so tests can exercise expiry without waiting.
RETIREMENT_GRACE_SECONDS = int(
    os.environ.get("NATIVE_DKIM_RETIREMENT_GRACE", "1800")
)


def retirement_marker_path(domain: str, selector: str) -> pathlib.Path:
    return KEY_DIR / f"{domain}.{selector}.key{RETIREMENT_SUFFIX}"


def retire(domain: str, selector: str, when: float | None = None) -> str:
    """
    Mark a selector's active key as retired, keeping the key itself.

    Written atomically and fsynced, because a half-written marker would make the
    retirement timestamp unreadable and the key would look like an orphan.

    Idempotent in the sense that re-retiring refreshes the timestamp; the caller
    is the lifecycle, which holds the advisory lock, so there is no race here.
    """
    _ensure_key_dir()
    target = retirement_marker_path(domain, selector)
    stamp = str(int(when if when is not None else time.time()))
    handle, temp_name = tempfile.mkstemp(dir=str(KEY_DIR), prefix=f".{domain}.", suffix=".rtmp")
    try:
        with os.fdopen(handle, "w", encoding="ascii") as fh:
            fh.write(stamp)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    _fsync_dir()
    logger.info("retired DKIM selector %s for %s; key kept for the grace period",
                selector, domain)
    return str(target)


def unretire(domain: str, selector: str) -> bool:
    """
    Drop a retirement marker. Returns whether one was there.

    Used when a selector is (still) the authoritative one — either because a
    rotation was rolled back, or because a crash left a marker on a selector the
    database never stopped naming. A marker on the live selector would eventually
    expire and delete the key the engine is actively signing with.
    """
    marker = retirement_marker_path(domain, selector)
    try:
        marker.unlink()
        logger.info("cleared the retirement marker on %s for %s", selector, domain)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise DkimError(f"could not clear retirement marker for {domain}: {exc}") from exc


def retired_at(domain: str, selector: str) -> int | None:
    """Epoch seconds the selector was retired, or None if it is not retired."""
    marker = retirement_marker_path(domain, selector)
    try:
        raw = marker.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        # An unreadable timestamp must not mean "delete immediately". Fall back
        # to the file's own mtime, which is at least as recent as the write.
        try:
            return int(marker.stat().st_mtime)
        except OSError:
            return None


def retirement_expired(domain: str, selector: str, now: float | None = None) -> bool:
    """
    Has a retired key outlived its grace period?

    A selector with no marker is NOT retired, and this returns False — an
    unmarked stray is an orphan, which is a different judgement made elsewhere.
    """
    stamp = retired_at(domain, selector)
    if stamp is None:
        return False
    current = now if now is not None else time.time()
    return (current - stamp) >= RETIREMENT_GRACE_SECONDS


def retired_selectors(domain: str | None = None) -> list[tuple[str, str]]:
    """Every (domain, selector) currently carrying a retirement marker."""
    found = []
    for marker in sorted(KEY_DIR.glob(f"*.key{RETIREMENT_SUFFIX}")):
        stem = marker.name[: -len(f".key{RETIREMENT_SUFFIX}")]
        marker_domain, _, marker_selector = stem.rpartition(".")
        if not marker_domain or not marker_selector:
            continue
        if domain is None or marker_domain == domain:
            found.append((marker_domain, marker_selector))
    return found


def remove_retired(domain: str, selector: str) -> bool:
    """
    Delete a retired key and its marker, in that order.

    Key first: if the marker went first and the process died, the key would be
    left looking like an unmarked orphan — still removable, but by a rule that
    was never meant to decide it.
    """
    removed = remove(key_path(domain, selector))
    with contextlib.suppress(OSError):
        retirement_marker_path(domain, selector).unlink()
    if removed:
        logger.info("removed retired DKIM key %s for %s after its grace period",
                    selector, domain)
    return removed


def stale_staging_files() -> list[pathlib.Path]:
    """
    Temporary artefacts from an interrupted write or activation.

    Both are created with a leading dot and a distinctive suffix, so they are
    identifiable without guessing. They never contain a key anything refers to.
    """
    return [p for p in KEY_DIR.glob(".*")
            if p.name.endswith(".tmp") or ".activate-" in p.name]
