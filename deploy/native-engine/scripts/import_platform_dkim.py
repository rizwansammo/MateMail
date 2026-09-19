#!/usr/bin/env python3
"""
NE6 one-time utility: adopt an EXISTING DKIM private key into the Native Engine.

WHY THIS EXISTS AT ALL
    The engine's normal lifecycle is that it generates its own keys and nothing
    ever hands it private material (DEC-007r). NE6 needs one deliberate
    exception. `mm1._domainkey.mail.matemail.online` is already published in
    public DNS and already signing MateMail's transactional mail through
    Mailcow. Generating a fresh key would mean a DNS change, and a window in
    which mail is signed with a key the world has not seen yet. Adopting the
    existing key means NE6 requires no DNS change at all.

WHY IT IS A SCRIPT AND NOT AN API ROUTE
    An HTTP endpoint that accepts private keys is a permanent liability: it
    exists on every future deployment, for every domain, forever, and it is one
    authorisation bug away from being the worst endpoint in the system. This is
    a file that is copied in, run once, and deleted. It is invoked inside the
    API container, which is the only place that already holds DKIM material,
    and it reads the key from STDIN so the key never appears in a command line,
    a process listing, an environment variable or a shell history.

WHAT IT REFUSES
    Everything it is not for. One domain, one selector, both named explicitly
    on the command line; an RSA private key in PEM; at least the engine's
    minimum key size. It derives the public key itself and, when given
    --expect-public-sha256, refuses to install anything whose public half does
    not match what DNS already publishes. That check is the point: it makes
    "the key we installed is the key the world validates against" a fact rather
    than a hope.

NEVER LOGGED
    No private material is printed, logged, or written anywhere except the key
    file itself, at 0600, by the engine's own atomic writer. Errors are worded
    so that no branch can reveal key bytes.

USAGE (inside the API container, key on stdin)
    docker exec -i matemail-native-api python3 /tmp/import_platform_dkim.py \
        --domain mail.matemail.online --selector mm1 \
        --expect-public-sha256 <hex> < key.pem
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import tempfile

# The engine is not a package: `app.py` imports its siblings as top-level
# modules, which works because running it puts its own directory on sys.path.
# This script has to reproduce that, or it would import a DIFFERENT dkim module
# and write keys somewhere the engine does not look.
sys.path.insert(0, os.environ.get("NATIVE_APP_DIR", "/opt/matemail/native_api"))

from cryptography.hazmat.primitives import serialization              # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa             # noqa: E402

import db                                                             # noqa: E402
import dkim as dkim_lib                                               # noqa: E402
import validation                                                     # noqa: E402


class Refused(Exception):
    """A validation failure. Its message never contains key material."""


def public_sha256(public_b64: str) -> str:
    """
    Fingerprint of the base64 DER public key, the same string DNS publishes in
    the `p=` tag. Comparing fingerprints lets three systems be proven identical
    without any of them printing a key.
    """
    return hashlib.sha256(public_b64.encode("ascii")).hexdigest()


def load_private_key(pem: bytes):
    if not pem.strip():
        raise Refused("no key was supplied on stdin")
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception:
        # Deliberately not chaining the original exception: some PEM parsers
        # include offending bytes in their message.
        raise Refused("stdin is not a readable unencrypted PEM private key") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise Refused("only RSA DKIM keys are supported by this engine")
    if key.key_size < dkim_lib.MINIMUM_KEY_SIZE:
        raise Refused(
            "refusing a %d-bit key; the engine minimum is %d"
            % (key.key_size, dkim_lib.MINIMUM_KEY_SIZE))
    return key


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--selector", required=True)
    ap.add_argument("--expect-public-sha256", default="",
                    help="refuse unless the derived public key has this "
                         "fingerprint; use the one taken from public DNS")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and print the fingerprint, install nothing")
    args = ap.parse_args()

    domain = args.domain.strip().lower()
    selector = args.selector.strip()
    if not domain or not selector:
        raise Refused("domain and selector are both required")
    # The engine's own validator, so this cannot accept a name the rest of
    # the engine would reject.
    domain = validation.domain_name(domain)

    pem = sys.stdin.buffer.read()
    try:
        private_key = load_private_key(pem)
        public_b64 = dkim_lib._public_from_private(private_key)
        fingerprint = public_sha256(public_b64)

        if args.expect_public_sha256:
            expected = args.expect_public_sha256.strip().lower()
            if fingerprint != expected:
                raise Refused(
                    "the supplied key's public half does not match the "
                    "expected fingerprint; refusing to install it "
                    "(expected %s, derived %s)" % (expected[:16], fingerprint[:16]))

        print("domain           %s" % domain)
        print("selector         %s" % selector)
        print("key_size         %d" % private_key.key_size)
        print("public_sha256    %s" % fingerprint)

        if args.dry_run:
            print("dry-run          nothing was written")
            return 0

        # ── Install, reusing the engine's own atomic writer ─────────────────
        #
        # Written to an immutable generation file first, exactly as `generate`
        # does, then activated. Nothing is live until the activate step, so a
        # crash here leaves the previous state untouched.
        dkim_lib._ensure_key_dir()
        token = os.urandom(8).hex()
        target = dkim_lib.generation_path(domain, selector, token)
        normalised = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        handle, temp_name = tempfile.mkstemp(
            dir=str(dkim_lib.KEY_DIR), prefix=".%s." % domain, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as fh:
                os.fchmod(fh.fileno(), dkim_lib._PRIVATE_MODE)
                fh.write(normalised)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temp_name, target)
            dkim_lib._fsync_dir()
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        finally:
            del normalised

        dkim_lib.activate(target, dkim_lib.key_path(domain, selector))

        # ── Engine state, in one transaction ────────────────────────────────
        # The engine's own DKIM lock, so this cannot interleave with a
        # rotation or a reconciliation running at the same moment.
        conn = db.connect()
        try:
            with conn, db.dkim_lifecycle_lock(conn):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO dkim_key
                               (domain_name, selector, public_key,
                                private_key_path, key_size, active)
                        VALUES (%s, %s, %s, %s, %s, true)
                        ON CONFLICT (domain_name) DO UPDATE SET
                            selector         = EXCLUDED.selector,
                            public_key       = EXCLUDED.public_key,
                            private_key_path = EXCLUDED.private_key_path,
                            key_size         = EXCLUDED.key_size,
                            active           = true,
                            rotated_at       = now()
                        """,
                        (domain, selector, public_b64,
                         str(dkim_lib.key_path(domain, selector)),
                         private_key.key_size),
                    )
                    cur.execute(
                        "SELECT domain_name, selector FROM dkim_key "
                        "WHERE active ORDER BY domain_name")
                    pairs = [(r[0], r[1]) for r in cur.fetchall()]
        finally:
            conn.close()

        # Rspamd resolves a domain's selector from this file and re-reads it on
        # change, so it is written last: the key is already in place by the time
        # anything can act on the mapping.
        dkim_lib.write_selector_map(pairs)

        mode = dkim_lib.private_key_mode(dkim_lib.key_path(domain, selector))
        print("private_key_mode %s" % (oct(mode) if mode is not None else "unknown"))
        print("selectors_map    %d entry(ies)" % len(pairs))
        print("installed        yes")
        return 0
    finally:
        del pem


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as exc:
        print("refused: %s" % exc, file=sys.stderr)
        sys.exit(2)
