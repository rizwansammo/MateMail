"""Durable, mailbox-scoped Dovecot session-disconnect reconciliation.

A credential/policy mutation queues this job in its *own database transaction*
before it commits. No worker may acknowledge a different generation after an
overlapping mutation. The feature remains disabled until full release gates pass.
"""
from __future__ import annotations

import logging
import os
import threading

import operations

logger = logging.getLogger("matemail.native.api.kick_recovery")
POLL_SECONDS = 5
MAX_BACKOFF_SECONDS = 3600


def enabled() -> bool:
    return os.environ.get("NATIVE_CLIENT_KICK_RECOVERY_ENABLED", "").lower() == "true"


def enqueue(conn, address: str) -> tuple[int, str]:
    """Queue an exact mailbox kick atomically with an authorized mutation.

    The API's credential/policy handler has already validated and locked the
    mailbox. Each new generation supersedes any previous acknowledgement.
    """
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO public.mail_client_kick_pending(mailbox_id)
               SELECT id FROM public.mailbox WHERE address=%s
               ON CONFLICT (mailbox_id) DO UPDATE
                   SET generation=gen_random_uuid(), attempts=0,
                       next_attempt_at=now(), last_failure_at=NULL, updated_at=now()
               RETURNING mailbox_id, generation::text""",
            (address,),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("cannot queue a disconnect for a missing mailbox")
        return int(row[0]), str(row[1])


def acknowledge(conn, mailbox_id: int, generation: str) -> None:
    """Never clear a newer queued disconnect after a concurrent mutation."""
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM public.mail_client_kick_pending
               WHERE mailbox_id=%s AND generation=%s::uuid""",
            (mailbox_id, generation),
        )


def backoff_seconds(attempts: int) -> int:
    return min(MAX_BACKOFF_SECONDS, 5 * (2 ** min(max(attempts, 0), 10)))


def recover_one(conn) -> bool:
    """Retry one due mailbox using a PostgreSQL row lock / SKIP LOCKED.

    The connection commits after this function returns. Keep the row locked
    until the Dovecot request finishes to prevent a lost newer generation.
    All failures keep the job and back off; the queue survives API restarts.
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT q.mailbox_id, q.generation::text, m.address, q.attempts
               FROM public.mail_client_kick_pending q
               JOIN public.mailbox m ON m.id=q.mailbox_id
               WHERE q.next_attempt_at <= now()
               ORDER BY q.next_attempt_at, q.mailbox_id
               LIMIT 1 FOR UPDATE OF q SKIP LOCKED"""
        )
        row = cur.fetchone()
    if row is None:
        return False

    mailbox_id, generation, address, attempts = row
    try:
        operations.kick_mailbox_sessions(address)
    except Exception as exc:  # noqa: BLE001 — retain job for *all* failures
        # Do not log exception text: future transport drivers can embed secrets.
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE public.mail_client_kick_pending
                   SET attempts=attempts+1,
                       next_attempt_at=now()+(%s * interval '1 second'),
                       last_failure_at=now(), updated_at=now()
                   WHERE mailbox_id=%s AND generation=%s::uuid""",
                (backoff_seconds(attempts), mailbox_id, generation),
            )
        logger.warning("pending IMAP disconnect retry failed: %s", type(exc).__name__)
        return True

    acknowledge(conn, mailbox_id, generation)
    return True


def run_worker(connect, stop: threading.Event) -> None:
    """Bounded, interruption-safe worker; never touches production when OFF."""
    while not stop.is_set():
        try:
            with connect() as conn:
                handled = recover_one(conn)
        except Exception as exc:  # noqa: BLE001 — next poll after DB outage
            logger.warning("IMAP disconnect reconciliation unavailable: %s", type(exc).__name__)
            handled = False
        stop.wait(0.25 if handled else POLL_SECONDS)


def start_worker(connect) -> tuple[threading.Event, threading.Thread]:
    stop = threading.Event()
    worker = threading.Thread(
        target=run_worker,
        args=(connect, stop),
        name="native-imap-disconnect-recovery",
        daemon=True,
    )
    worker.start()
    return stop, worker
