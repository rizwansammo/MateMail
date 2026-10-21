"""Real isolated IMAPS/SMTP organization 2FA acceptance (GH Actions only).

This script runs INSIDE the disposable candidate Native API container. It uses
only synthetic .invalid addresses, temporary database credentials and the
internal Docker network. Its assertions run real IMAPS and SMTP STARTTLS AUTH
against candidate Dovecot/Postfix, not a mocked password checker.
"""
from __future__ import annotations

import imaplib
import json
import os
import pathlib
import smtplib
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid

# The candidate native API is baked into this throwaway image.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import db
import kick_recovery

ALPHA = str(uuid.UUID("15fd496f-e91e-48e6-98cf-32f61e4c4c14"))
BETA = str(uuid.UUID("4e6f9a87-7d32-4c3d-b761-564a193c9254"))
A = "alice@alpha-ga.invalid"
B = "bob@beta-ga.invalid"
NEW = "new@alpha-ga.invalid"
PRIMARY = "ga-only-primary-2026"
BASE_URL = "http://127.0.0.1:8451"
API_SECRET = "ga-test-api-only"
TLS = ssl.create_default_context()
TLS.check_hostname = False
TLS.verify_mode = ssl.CERT_NONE


def accepted(label: str) -> None:
    print("WIRE_ACCEPTANCE_PASS:", label, flush=True)


def api(path: str, payload: dict):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE_URL + path, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Native-Api-Secret": API_SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            body = json.load(resp)
            if resp.status != 200:
                raise AssertionError(f"Internal API returned {resp.status}: {path}")
            return body
    except urllib.error.HTTPError as exc:
        # Never print payload or passwords; error text is enough for diagnosis.
        raise AssertionError(
            f"Internal API failed {path} with HTTP {exc.code}: "
            f"{exc.read(400).decode(errors='replace')}"
        ) from None


def with_db(statement: str, args=(), *, fetch=False):
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(statement, args)
            if fetch:
                return cur.fetchall()


def wait_native_ready():
    for _ in range(60):
        try:
            version = with_db(
                "SELECT coalesce(max(version),0) FROM public.schema_version",
                fetch=True,
            )[0][0]
            if version == 16:
                return
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError("Native API migrations / DB not ready at 16")


def create_domain(name, tenant):
    with_db(
        "INSERT INTO public.domain (name,tenant_id) VALUES (%s,%s::uuid)",
        (name, tenant),
    )


def seed_mailbox(address):
    """Bootstrap two synthetic existing users only; never access production."""
    with_db(
        """INSERT INTO public.mailbox
           (domain_id,address,local_part,password_hash,login_enabled)
           SELECT d.id,%s,%s,
                  '{BLF-CRYPT}'||public.crypt(%s,public.gen_salt('bf',4)),true
             FROM public.domain d WHERE d.name=%s""",
        (address, address.split("@")[0], PRIMARY, address.rsplit("@", 1)[1]),
    )


def imap_login(address, password, *, allow: bool):
    client = None
    try:
        client = imaplib.IMAP4_SSL("dovecot", 993, ssl_context=TLS, timeout=8)
        result, _ = client.login(address, password)
        if result != "OK":
            raise AssertionError(f"Unexpected IMAPS status {result}")
        if not allow:
            raise AssertionError(f"UNSAFE: IMAPS allowed primary credentials for {address}")
        return client
    except imaplib.IMAP4.error:
        if allow:
            raise AssertionError(f"IMAPS rejected intended allowed user {address}") from None
        return None
    finally:
        if not allow and client is not None:
            try:
                client.logout()
            except Exception:
                pass


def close_imap(client):
    if client is not None:
        try:
            client.logout()
        except Exception:
            pass


def smtp_login(address, password, *, allow: bool):
    try:
        with smtplib.SMTP("postfix", 587, timeout=8) as smtp:
            smtp.ehlo()
            if not smtp.has_extn("starttls"):
                raise AssertionError("STARTTLS missing from SMTP 587")
            smtp.starttls(context=TLS)
            smtp.ehlo()
            smtp.login(address, password)
        if not allow:
            raise AssertionError(f"UNSAFE: SMTP AUTH accepted forbidden password for {address}")
    except smtplib.SMTPAuthenticationError:
        if allow:
            raise AssertionError(f"SMTP AUTH rejected intended allowed user {address}") from None


def assert_session_terminated(client, label):
    for _ in range(14):
        time.sleep(0.4)
        try:
            client.noop()
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error, OSError, ssl.SSLError, EOFError):
            accepted(label)
            return
    raise AssertionError(f"Existing authenticated IMAP session persisted: {label}")


def assert_session_healthy(client, label):
    status, _ = client.noop()
    assert status == "OK", f"Unchanged policy unexpectedly disconnected {label}"
    accepted(label)


def main():
    # A hard guard: the script cannot point at an existing DNS domain or a
    # production database even if a developer copies it to another runner.
    assert os.environ.get("NATIVE_DB_HOST") == "pg"
    assert os.environ.get("NATIVE_DB_NAME") == "matemail_engine"
    assert os.environ.get("NATIVE_API_SECRET") == API_SECRET
    assert os.environ.get("NATIVE_CLIENT_KICK_RECOVERY_ENABLED") == "true"
    wait_native_ready()
    create_domain("alpha-ga.invalid", ALPHA)
    create_domain("beta-ga.invalid", BETA)
    seed_mailbox(A)
    seed_mailbox(B)

    # Baseline: both tenants can authenticate through the real protocol stack.
    live_a = imap_login(A, PRIMARY, allow=True)
    live_b = imap_login(B, PRIMARY, allow=True)
    smtp_login(A, PRIMARY, allow=True)
    smtp_login(B, PRIMARY, allow=True)
    accepted("S0 legacy IMAPS and SMTP login before any org policy")

    # S1: un-enrolled Required mailbox cannot authenticate via SMTP or IMAPS.
    changed = api("/v1/tenants/mfa/require", {
        "tenant_id": ALPHA, "domains": ["alpha-ga.invalid"], "required": True,
    })
    assert changed["required"] is True
    assert_session_terminated(live_a, "S4 existing Alpha IMAP session kicked on Enforce")
    imap_login(A, PRIMARY, allow=False)
    smtp_login(A, PRIMARY, allow=False)
    accepted("S1 unenrolled Required org rejects original password over IMAPS and SMTP")

    # S2: unrelated organization continues with both protocols; the previous
    # authenticated session also survives the other tenant's Require change.
    assert_session_healthy(live_b, "S2 Beta live IMAP session survives Alpha Enforce")
    imap_login(B, PRIMARY, allow=True).logout()
    smtp_login(B, PRIMARY, allow=True)
    accepted("S2 separate tenant remains unaffected via IMAPS and SMTP")

    # S3: a mailbox provisioned AFTER Enforce is protected by tenant binding.
    api("/v1/mailboxes/ensure", {"address": NEW, "password": PRIMARY,
                                  "login_enabled": True})
    imap_login(NEW, PRIMARY, allow=False)
    smtp_login(NEW, PRIMARY, allow=False)
    accepted("S3 newly provisioned mailbox inherits Require for both protocols")

    # App-specific password, not original password, can authenticate after
    # explicit mailbox enrollment/policy. The earlier canary accepted the
    # TOTP UX; this stage validates tenant Require's wire-level consequence.
    issued = api("/v1/mailboxes/client/issue", {"address": A, "label": "GA synthetic"})
    app_password = issued["one_time_credential"]
    api("/v1/mailboxes/client/policy", {
        "address": A, "enabled": True, "dedicated_required": True,
    })
    app_live = imap_login(A, app_password, allow=True)
    smtp_login(A, app_password, allow=True)
    imap_login(A, PRIMARY, allow=False)
    smtp_login(A, PRIMARY, allow=False)
    accepted("S1/S4 dedicated app password works while legacy primary stays denied")

    # Revocation must disconnect an already authorized IMAP connection as
    # well as deny fresh IMAP/SMTP logins (existing Canary acceptance extended
    # here to an actively Required tenant).
    api("/v1/mailboxes/client/revoke", {"address": A, "id": issued["id"]})
    assert_session_terminated(app_live, "S4 credential revocation kicks live IMAP")
    imap_login(A, app_password, allow=False)
    smtp_login(A, app_password, allow=False)
    accepted("S4 revoked app password denied via both mail protocols")

    # Exercise ACTUAL background recovery against a live, reconnected client:
    # queue a synthetic pending session kick directly in disposable DB, then
    # let the built-in recovery worker remove the job and disconnect the user.
    second = api("/v1/mailboxes/client/issue", {
        "address": A, "label": "Recovery synthetic"})
    fresh_live = imap_login(A, second["one_time_credential"], allow=True)
    with db.connect() as conn:
        kick_recovery.enqueue(conn, A)
    for _ in range(30):
        pending = with_db(
            """SELECT count(*) FROM public.mail_client_kick_pending p
               JOIN public.mailbox m ON m.id=p.mailbox_id WHERE m.address=%s""",
            (A,), fetch=True,
        )[0][0]
        if pending == 0:
            break
        time.sleep(0.5)
    assert pending == 0, "Background recovery did not acknowledge pending kick"
    assert_session_terminated(fresh_live, "S4 background recovery kicks live IMAP")
    accepted("S4 durable pending session-disconnect queue recovered")

    # Disable Require. This is Organization policy, not disabling voluntary
    # enrollment: unenrolled NEW user regains primary logins; enrolled Alpha
    # user must keep dedicated password requirement. Beta stays untouched.
    old_beta = imap_login(B, PRIMARY, allow=True)
    released = api("/v1/tenants/mfa/require", {
        "tenant_id": ALPHA, "domains": ["alpha-ga.invalid"], "required": False,
    })
    assert released["required"] is False
    imap_login(NEW, PRIMARY, allow=True).logout()
    smtp_login(NEW, PRIMARY, allow=True)
    imap_login(A, PRIMARY, allow=False)
    smtp_login(A, PRIMARY, allow=False)
    assert_session_healthy(old_beta, "S4 Beta session survives Alpha enforcement disable")
    close_imap(old_beta)
    accepted("S4 Disable returns unenrolled mailbox to normal; enrolled retains MFA")

    # Re-saving an unchanged Off policy must not needlessly terminate sessions.
    stable = imap_login(NEW, PRIMARY, allow=True)
    api("/v1/tenants/mfa/require", {
        "tenant_id": ALPHA, "domains": ["alpha-ga.invalid"], "required": False,
    })
    assert_session_healthy(stable, "S4 unchanged Off does not disconnect IMAP")
    close_imap(stable)
    close_imap(live_b)
    accepted("ALL_FOUR_ORGANIZATION_MFA_SCENARIOS")
    

if __name__ == "__main__":
    main()
