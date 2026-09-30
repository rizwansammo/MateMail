"""
The IMAP gateway: MateMail's only path to the mail store.

HOW IT CONNECTS
    Through the private gateway on the engine link network, which forwards 993
    to Dovecot and offers nothing else. Dovecot is deliberately not reachable
    from this application directly — joining it to the link network would also
    expose LMTP, where mail can be injected into any mailbox without
    authenticating, and the doveadm administrative API.

    Two ways in, and the difference matters:

      `authenticate(address, password)`  the person's own password, used ONCE
                                         at sign-in to prove who they are.
      `open_mailbox(address)`            the master identity, used for every
                                         request afterwards.

    The second exists so MateMail never has to keep the first. A retained
    mailbox password would have to live wherever the session lives; a master
    credential lives in one root-only file on one host.

WHAT THIS LAYER PROMISES
    * Connections are short-lived and always closed. Django workers are
      threads, not a connection pool, and an IMAP connection left open across
      requests is a file descriptor leak with a login attached.
    * UIDs, never sequence numbers. A sequence number changes when anything in
      the folder is expunged, so acting on one is acting on whatever moved into
      that position.
    * UIDVALIDITY travels with every UID. Without it a UID is meaningless after
      a folder is recreated, which is exactly what UIDVALIDITY exists to say.
    * Batched FETCH. Listing fifty messages is one round trip, not fifty.
"""
from __future__ import annotations

import contextlib
import email.utils
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass, field
from typing import Iterator

from django.conf import settings

logger = logging.getLogger(__name__)

#: IMAP's own limit is 10,000 octets per line for some servers; Dovecot's is
#: higher, but a single FETCH response for a large message can be megabytes.
imaplib._MAXLINE = max(imaplib._MAXLINE, 10_000_000)


class MailAccessError(Exception):
    """
    A MateMail-authored failure.

    Carries two messages on purpose. `customer_message` is what a PostBox user
    sees and says nothing about Dovecot, hostnames or internal addresses;
    `log_message` is what the operator gets. The same split the Mail Engine
    adapter uses, for the same reason — an IMAP error string is full of
    implementation detail that has no business in a browser.
    """

    def __init__(self, customer_message: str, log_message: str = ""):
        super().__init__(customer_message)
        self.customer_message = customer_message
        self.log_message = log_message or customer_message


class AuthenticationFailed(MailAccessError):
    """Credentials were refused. Deliberately indistinguishable from 'no such mailbox'."""


#: Dovecot's special-use attributes, mapped to the roles PostBox shows. The
#: attribute is authoritative — a folder called "Sent" that is not flagged
#: `\Sent` is just a folder, and the one flagged `\Sent` is Sent whatever it is
#: called in the user's language.
SPECIAL_USE = {
    "\\Sent": "sent",
    "\\Drafts": "drafts",
    "\\Trash": "trash",
    "\\Junk": "junk",
    "\\Archive": "archive",
}

#: Created if absent. `Scheduled` is MateMail's own: IMAP has no special-use
#: attribute for it, because scheduled send is not an IMAP concept.
REQUIRED_FOLDERS = ("Sent", "Drafts", "Trash", "Junk", "Archive", "Scheduled")

SPECIAL_USE_FOR_CREATE = {
    "Sent": "\\Sent",
    "Drafts": "\\Drafts",
    "Trash": "\\Trash",
    "Junk": "\\Junk",
    "Archive": "\\Archive",
}

#: Fallback when the server supplies no special-use attribute at all.
#:
#: Matched against the WHOLE folder name, case-insensitively — never a
#: prefix or a substring. "Old Sent", "Sent 2025" and "My Archive" are
#: ordinary folders somebody created, and promoting one to the real Sent
#: folder would file sent mail into it from then on, silently.
#:
#: WHY NOT REPAIR THE MAILBOXES INSTEAD
#: It is possible. RFC 6154 §5 defines the `/private/specialuse` mailbox
#: annotation, and a server implementing RFC 5464 METADATA MAY accept
#: SETMETADATA to set it on an existing folder — so the attributes could be
#: written onto the folders that lack them.
#:
#: Not done here, for three plain reasons: METADATA support on this server
#: has not been established, it is unnecessary — reading the name solves
#: the problem — and it would mean writing to every existing customer
#: mailbox to fix a presentation bug. If a retrofit is wanted later it
#: should be its own change, with its own capability check.
#:
#: `Spam` maps to `junk` because that is the same folder under a different
#: name, and `Scheduled` is MateMail's own — IMAP has no attribute for it
#: because scheduled send is not an IMAP concept.
#: Exactly the folders MateMail itself creates, plus INBOX and the Spam
#: spelling of Junk. Nothing speculative: `Sent Items` and `Deleted Items`
#: are Outlook's names and no mailbox here has been observed using them, so
#: adding them would be widening a heuristic on a guess. If a real mailbox
#: turns up needing one, add it then, with the mailbox as the evidence.
CANONICAL_ROLE_NAMES = {
    "INBOX": "inbox",
    "SENT": "sent",
    "DRAFTS": "drafts",
    "TRASH": "trash",
    "JUNK": "junk",
    "SPAM": "junk",
    "ARCHIVE": "archive",
    "SCHEDULED": "scheduled",
}


@dataclass
class FolderInfo:
    name: str
    role: str = ""           # inbox / sent / drafts / trash / junk / archive / ""
    flags: tuple[str, ...] = ()
    messages: int = 0
    unseen: int = 0
    uid_validity: int = 0
    selectable: bool = True


@dataclass
class MessageSummary:
    """Enough to render a list row. Never a body."""

    uid: int
    uid_validity: int
    folder: str
    message_id: str = ""
    subject: str = ""
    from_name: str = ""
    from_address: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    date: str = ""
    size: int = 0
    seen: bool = False
    flagged: bool = False
    answered: bool = False
    draft: bool = False
    has_attachments: bool = False
    thread_references: list[str] = field(default_factory=list)
    in_reply_to: str = ""


def _ssl_context() -> ssl.SSLContext:
    """
    TLS to the gateway, which forwards bytes to Dovecot untouched.

    The certificate Dovecot presents is for `mx.matemail.online`, and the
    gateway carries that name as a network alias — so when the host we dial is
    that name, verification is real end-to-end verification of Dovecot's own
    certificate, not of a proxy.

    Verification is only relaxed when an operator deliberately points
    POSTBOX_IMAP_HOST at an address instead of that name (development, or a
    direct container IP), because a certificate cannot match an IP it was not
    issued for. That is a configuration choice with a visible warning, not a
    default.
    """
    context = ssl.create_default_context()
    host = getattr(settings, "POSTBOX_IMAP_HOST", "")
    if getattr(settings, "POSTBOX_IMAP_VERIFY", True) and not _looks_like_ip(host):
        return context

    logger.warning(
        "PostBox IMAP certificate verification is disabled for host %r. This is "
        "only appropriate when dialling an address rather than a hostname.", host,
    )
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _looks_like_ip(host: str) -> bool:
    return bool(re.fullmatch(r"[\d.]+|\[?[0-9a-fA-F:]+\]?", host or ""))


class MailboxConnection:
    """
    One authenticated IMAP conversation, scoped to one mailbox.

    Used as a context manager so the LOGOUT always happens. Every method takes
    and returns UIDs; nothing here exposes a sequence number to a caller.
    """

    def __init__(self, imap: imaplib.IMAP4_SSL, address: str):
        self._imap = imap
        self.address = address
        self._selected: str | None = None
        self._uid_validity: int = 0

    # ── connection ──────────────────────────────────────────────────────────

    def __enter__(self) -> "MailboxConnection":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._imap.logout()

    # ── folders ─────────────────────────────────────────────────────────────

    def list_folders(self) -> list[FolderInfo]:
        """
        Every folder, with a role resolved in TWO passes.

        The passes are the whole point. Downstream code builds
        `{f.role: f.name for f in list_folders() if f.role}` — a dict, so
        for one role the LAST row wins. Resolving row by row therefore let
        LIST ordering decide which mailbox was Sent:

            (\\Sent) "Enviados"   -> sent     (the server said so)
            (no flags) "Sent"     -> sent     (a guess from the name)

        and if the guess came second it silently replaced the real one.
        Sent mail would then be filed into a folder the server never
        designated, for exactly the mailboxes that had it configured
        correctly.

        So: read every attribute first, record which roles the SERVER has
        claimed, and only then let names fill the roles still unclaimed.
        """
        status, rows = self._imap.list()
        if status != "OK":
            raise MailAccessError("Your folders could not be listed.", f"LIST -> {status}")

        parsed_rows: list[tuple[str, list[str], str]] = []
        claimed: set[str] = set()

        # ── pass 1: what the server actually said ───────────────────────
        for raw in rows or []:
            parsed = _parse_list_line(raw)
            if parsed is None:
                continue
            name, flags = parsed
            lowered = {f.lower() for f in flags}

            role = ""
            for attribute, mapped in SPECIAL_USE.items():
                if attribute.lower() in lowered:
                    role = mapped
                    claimed.add(mapped)
                    break

            parsed_rows.append((name, flags, role))

        # ── pass 2: names fill only what is still unclaimed ─────────────
        folders: list[FolderInfo] = []
        for name, flags, role in parsed_rows:
            if not role:
                candidate = CANONICAL_ROLE_NAMES.get(name.strip().upper(), "")
                # `claimed` grows as we go, so two unflagged folders that
                # map to the same role cannot both take it either — the
                # first in LIST order wins and the second stays custom.
                if candidate and candidate not in claimed:
                    role = candidate
                    claimed.add(candidate)

            folders.append(
                FolderInfo(
                    name=name,
                    role=role,
                    flags=tuple(flags),
                    selectable="\\noselect" not in (f.lower() for f in flags),
                )
            )
        return folders

    def _authenticated_capabilities(self) -> frozenset[str]:
        """
        CAPABILITY as the server reports it AFTER authentication.

        Not `imaplib`'s `self.capabilities`. That attribute is populated by
        `_get_capabilities()`, which imaplib calls from `__init__` and from
        `starttls()` and NOWHERE ELSE — `login()` does not refresh it. So it
        holds the PRE-authentication list for the lifetime of the
        connection, and most servers advertise a deliberately smaller set
        before login. Reading it to decide what the authenticated session
        supports answers the wrong question, and answers it 'no'.

        Issued once and cached on this connection: it is asked for every
        folder in `ensure_standard_folders`, and a round trip per folder on
        every sign-in would be six for an answer that cannot change.
        """
        cached = getattr(self, "_capability_cache", None)
        if cached is not None:
            return cached

        names: set[str] = set()
        try:
            status, data = self._imap.capability()
            if status == "OK":
                for chunk in data or []:
                    text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
                    names.update(part.upper() for part in text.split())
        except Exception as exc:  # noqa: BLE001
            # An empty set means 'plain CREATE', which is the safe answer:
            # a folder without its attribute is cosmetic, and failing a
            # sign-in because CAPABILITY misbehaved would not be.
            logger.info("PostBox: CAPABILITY failed (%r); assuming no extensions", exc)

        self._capability_cache = frozenset(names)
        return self._capability_cache

    def _supports_special_use_create(self) -> bool:
        """Whether the server accepts `CREATE name (USE (\\Sent))`."""
        return "CREATE-SPECIAL-USE" in self._authenticated_capabilities()

    def _create_folder(self, name: str, attribute: str | None) -> str:
        """
        CREATE, with the special-use attribute when the server understands it.

        Falls back to a plain CREATE otherwise — and also if the attributed
        form is refused, because a mailbox with a Sent folder that lacks an
        attribute is a cosmetic problem, while a mailbox with no Sent folder is
        a broken one.
        """
        if attribute and self._supports_special_use_create():
            try:
                status, _ = self._imap._simple_command(
                    "CREATE", _quote(name), f"(USE ({attribute}))"
                )
                if status == "OK":
                    return status
                logger.info(
                    "PostBox: CREATE %s with %s -> %s; retrying without it",
                    name, attribute, status,
                )
            except Exception as exc:  # noqa: BLE001 - see below
                # Deliberately broad, and deliberately not silent. imaplib
                # raises its own error class for a refused command, and the
                # only sensible response is the plain CREATE below — failing
                # sign-in because a folder could not be labelled would be a
                # much worse outcome than an unlabelled folder.
                logger.info(
                    "PostBox: CREATE %s with %s raised %r; retrying without it",
                    name, attribute, exc,
                )

        status, _ = self._imap.create(_quote(name))
        return status

    def ensure_standard_folders(self) -> list[str]:
        """
        Create the special-use folders that are missing. Idempotent.

        Idempotence is the point: this runs on every sign-in, and a version
        that created "Sent" each time would give a mailbox Sent, Sent1, Sent2.
        Existing folders are detected by their special-use attribute first and
        their name second, so a mailbox that already has a localised Sent
        folder does not gain an English duplicate.
        """
        existing = self.list_folders()
        have_roles = {f.role for f in existing if f.role}
        have_names = {f.name.upper() for f in existing}

        created: list[str] = []
        for name in REQUIRED_FOLDERS:
            attribute = SPECIAL_USE_FOR_CREATE.get(name)
            role = SPECIAL_USE.get(attribute or "", "")
            if role and role in have_roles:
                continue
            if name.upper() in have_names:
                continue

            status = self._create_folder(name, attribute)
            if status != "OK":
                # A folder that already exists in a form we did not recognise
                # is not an error worth failing a sign-in over.
                logger.info("PostBox: CREATE %s -> %s", name, status)
                continue
            created.append(name)
            if attribute:
                # Dovecot accepts SETMETADATA for special-use; if it declines,
                # the folder still works, it is just not tagged.
                with contextlib.suppress(Exception):
                    self._imap.subscribe(_quote(name))
        return created

    def create_folder(self, name: str) -> None:
        _reject_folder_name(name)
        status, detail = self._imap.create(_quote(name))
        if status != "OK":
            raise MailAccessError(
                "That folder could not be created.", f"CREATE {name!r} -> {detail}"
            )
        with contextlib.suppress(Exception):
            self._imap.subscribe(_quote(name))

    def rename_folder(self, old: str, new: str) -> None:
        _reject_folder_name(new)
        status, detail = self._imap.rename(_quote(old), _quote(new))
        if status != "OK":
            raise MailAccessError(
                "That folder could not be renamed.", f"RENAME -> {detail}"
            )

    def delete_folder(self, name: str) -> None:
        _reject_folder_name(name)
        if name.upper() == "INBOX":
            raise MailAccessError("The Inbox cannot be deleted.")
        status, detail = self._imap.delete(_quote(name))
        if status != "OK":
            raise MailAccessError(
                "That folder could not be deleted.", f"DELETE -> {detail}"
            )

    # ── selection ───────────────────────────────────────────────────────────

    def select(self, folder: str, readonly: bool = False) -> FolderInfo:
        status, data = self._imap.select(_quote(folder), readonly=readonly)
        if status != "OK":
            raise MailAccessError(
                "That folder could not be opened.", f"SELECT {folder!r} -> {data}"
            )
        self._selected = folder
        self._uid_validity = self._read_uid_validity()
        total = int(data[0]) if data and data[0] else 0
        return FolderInfo(
            name=folder,
            messages=total,
            uid_validity=self._uid_validity,
            unseen=self.count_unseen(),
        )

    def _read_uid_validity(self) -> int:
        with contextlib.suppress(Exception):
            status, data = self._imap.status(_quote(self._selected or "INBOX"), "(UIDVALIDITY)")
            if status == "OK" and data:
                found = re.search(rb"UIDVALIDITY\s+(\d+)", data[0])
                if found:
                    return int(found.group(1))
        return 0

    @property
    def uid_validity(self) -> int:
        return self._uid_validity

    def count_unseen(self) -> int:
        status, data = self._imap.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return 0
        return len(data[0].split())

    def folder_counts(self, folder: str) -> tuple[int, int]:
        """(total, unseen) without changing the current selection."""
        status, data = self._imap.status(_quote(folder), "(MESSAGES UNSEEN)")
        if status != "OK" or not data:
            return (0, 0)
        text = data[0].decode("utf-8", "replace")
        total = re.search(r"MESSAGES\s+(\d+)", text)
        unseen = re.search(r"UNSEEN\s+(\d+)", text)
        return (int(total.group(1)) if total else 0, int(unseen.group(1)) if unseen else 0)

    # ── listing ─────────────────────────────────────────────────────────────

    def search_uids(
        self,
        criteria: list[str] | None = None,
        *,
        newest: bool = True,
    ) -> list[int]:
        """
        UIDs matching a search in the requested mailbox order.

        UID order is used rather than IMAP SORT because SORT is optional while
        UID ordering is guaranteed inside one folder.  Ascending gives the
        oldest mailbox order and descending the newest mailbox order without
        requiring an extension.
        """
        args = criteria or ["ALL"]
        status, data = self._imap.uid("SEARCH", None, *args)
        if status != "OK":
            raise MailAccessError("That search could not be completed.", f"SEARCH -> {data}")
        if not data or not data[0]:
            return []
        return sorted((int(uid) for uid in data[0].split()), reverse=newest)

    def fetch_summaries(self, uids: list[int]) -> list[MessageSummary]:
        """
        Headers, flags, size and structure for a page of messages — ONE FETCH.

        The N+1 version of this is the single easiest way to make a webmail
        client unusable: fifty messages become fifty round trips, each with its
        own latency, and the list takes seconds instead of milliseconds.
        """
        if not uids:
            return []

        wanted = (
            "(UID FLAGS RFC822.SIZE BODYSTRUCTURE "
            "BODY.PEEK[HEADER.FIELDS "
            "(FROM TO CC SUBJECT DATE MESSAGE-ID REFERENCES IN-REPLY-TO)])"
        )
        status, data = self._imap.uid("FETCH", ",".join(str(u) for u in uids), wanted)
        if status != "OK":
            raise MailAccessError("Those messages could not be read.", f"FETCH -> {status}")

        summaries = {s.uid: s for s in _parse_fetch(data, self._selected or "", self._uid_validity)}
        # Returned in the order asked for, which is the order the caller paged.
        return [summaries[u] for u in uids if u in summaries]

    def fetch_raw(self, uid: int) -> bytes:
        status, data = self._imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not data or not data[0]:
            raise MailAccessError("That message could not be read.", f"FETCH raw -> {status}")
        for part in data:
            if isinstance(part, tuple) and len(part) > 1:
                return part[1]
        raise MailAccessError("That message could not be read.", "FETCH raw -> no literal")

    # ── flags ───────────────────────────────────────────────────────────────

    def store_flags(self, uids: list[int], flags: str, add: bool) -> None:
        if not uids:
            return
        command = "+FLAGS.SILENT" if add else "-FLAGS.SILENT"
        status, detail = self._imap.uid(
            "STORE", ",".join(str(u) for u in uids), command, f"({flags})"
        )
        if status != "OK":
            raise MailAccessError(
                "That change could not be saved.", f"STORE {flags} -> {detail}"
            )

    def mark_seen(self, uids: list[int], seen: bool) -> None:
        self.store_flags(uids, "\\Seen", add=seen)

    def mark_flagged(self, uids: list[int], flagged: bool) -> None:
        self.store_flags(uids, "\\Flagged", add=flagged)

    # ── moving ──────────────────────────────────────────────────────────────

    def move(self, uids: list[int], destination: str) -> None:
        """
        MOVE when the server has it, COPY + \\Deleted + EXPUNGE when it does not.

        The fallback is ordered so a failure leaves a copy rather than losing
        one: COPY first, and only then mark the original deleted. UID EXPUNGE
        is used where available so the expunge touches exactly these messages
        and not somebody else's deleted mail in the same folder.
        """
        if not uids:
            return
        uid_list = ",".join(str(u) for u in uids)

        if self._has_capability("MOVE"):
            status, detail = self._imap.uid("MOVE", uid_list, _quote(destination))
            if status != "OK":
                raise MailAccessError(
                    "Those messages could not be moved.", f"UID MOVE -> {detail}"
                )
            return

        status, detail = self._imap.uid("COPY", uid_list, _quote(destination))
        if status != "OK":
            raise MailAccessError(
                "Those messages could not be moved.", f"UID COPY -> {detail}"
            )
        self.store_flags(uids, "\\Deleted", add=True)
        if self._has_capability("UIDPLUS"):
            self._imap.uid("EXPUNGE", uid_list)
        else:
            self._imap.expunge()

    def delete_permanently(self, uids: list[int]) -> None:
        """Irreversible. Only ever called with explicit intent from the caller."""
        if not uids:
            return
        self.store_flags(uids, "\\Deleted", add=True)
        if self._has_capability("UIDPLUS"):
            self._imap.uid("EXPUNGE", ",".join(str(u) for u in uids))
        else:
            self._imap.expunge()

    def append(self, folder: str, raw: bytes, flags: str = "", when=None) -> tuple[int, int]:
        """
        Put a message into a folder and return (uid_validity, uid).

        The UID comes from the APPENDUID response when the server supports
        UIDPLUS. Without it there is no reliable way to learn the UID of what
        was just appended — searching by Message-ID afterwards is a guess that
        races with delivery — so the caller is told 0 and must cope.
        """
        status, detail = self._imap.append(
            _quote(folder), f"({flags})" if flags else None, when, raw
        )
        if status != "OK":
            raise MailAccessError("That message could not be saved.", f"APPEND -> {detail}")

        for blob in detail or []:
            found = re.search(rb"APPENDUID\s+(\d+)\s+(\d+)", blob if isinstance(blob, bytes) else b"")
            if found:
                return int(found.group(1)), int(found.group(2))
        return (0, 0)

    # ── capabilities ────────────────────────────────────────────────────────

    def _has_capability(self, name: str) -> bool:
        caps = getattr(self._imap, "capabilities", ()) or ()
        return name.upper() in {c.upper() for c in caps}

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(getattr(self._imap, "capabilities", ()) or ())


# ── connecting ──────────────────────────────────────────────────────────────

def _connect() -> imaplib.IMAP4_SSL:
    host = getattr(settings, "POSTBOX_IMAP_HOST", "mx.matemail.online")
    port = int(getattr(settings, "POSTBOX_IMAP_PORT", 993))
    timeout = int(getattr(settings, "POSTBOX_IMAP_TIMEOUT", 20))
    try:
        return imaplib.IMAP4_SSL(host, port, ssl_context=_ssl_context(), timeout=timeout)
    except (OSError, ssl.SSLError) as exc:
        raise MailAccessError(
            "Your mailbox is temporarily unavailable. Please try again shortly.",
            f"IMAP connect {host}:{port} failed: {exc!r}",
        ) from exc


def authenticate(address: str, password: str) -> bool:
    """
    Verify a mailbox password against Dovecot. Nothing is retained.

    This is the ONLY place a customer's mailbox password is handled, it is
    never written anywhere, and the connection it opens is closed immediately —
    the session that follows is opened through the master identity instead.
    """
    imap = _connect()
    try:
        imap.login(address, password)
    except imaplib.IMAP4.error as exc:
        # Wrong password, unknown mailbox and disabled account all land here,
        # and the caller must not be able to tell them apart.
        logger.info("PostBox authentication refused for %s: %s", _redact(address), exc)
        return False
    finally:
        with contextlib.suppress(Exception):
            imap.logout()
    return True


@contextlib.contextmanager
def open_mailbox(address: str) -> Iterator[MailboxConnection]:
    """
    An authenticated connection to `address`, opened as the master identity.

    The caller has already been authorised by the PostBox session; this is how
    the server reads that one mailbox without holding the person's password.
    """
    master_user = getattr(settings, "POSTBOX_MASTER_USER", "postbox")
    master_password = getattr(settings, "POSTBOX_MASTER_PASSWORD", "")
    separator = getattr(settings, "POSTBOX_MASTER_SEPARATOR", "*")

    if not master_password:
        raise MailAccessError(
            "Mailbox access is not configured. Please contact your administrator.",
            "POSTBOX_MASTER_PASSWORD is unset — PostBox cannot open any mailbox.",
        )

    imap = _connect()
    try:
        imap.login(f"{address}{separator}{master_user}", master_password)
    except imaplib.IMAP4.error as exc:
        with contextlib.suppress(Exception):
            imap.logout()
        raise MailAccessError(
            "Your mailbox could not be opened.",
            f"master login failed for {_redact(address)}: {exc}",
        ) from exc

    connection = MailboxConnection(imap, address)
    try:
        yield connection
    finally:
        connection.close()


def _redact(address: str) -> str:
    """Addresses in logs are reduced to their domain — the useful half."""
    _, _, domain = (address or "").partition("@")
    return f"<user>@{domain}" if domain else "<address>"


# ── parsing helpers ─────────────────────────────────────────────────────────

_LIST_RE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.*)')


def _parse_list_line(raw) -> tuple[str, list[str]] | None:
    if isinstance(raw, tuple):
        raw = b"".join(part for part in raw if isinstance(part, bytes))
    if not isinstance(raw, bytes):
        return None
    found = _LIST_RE.match(raw)
    if not found:
        return None
    flags = [f.decode("ascii", "replace") for f in found.group("flags").split()]
    name = found.group("name").strip()
    if name.startswith(b'"') and name.endswith(b'"'):
        name = name[1:-1]
    return _decode_folder(name), flags


def _decode_folder(raw: bytes) -> str:
    """
    Modified UTF-7, which is how IMAP has always spelled non-ASCII folder names.

    A folder called "Wichtig" is fine either way; one called "Wichtig ü" comes
    back as `Wichtig &APw-` and would otherwise be displayed literally.
    """
    try:
        return raw.decode("ascii").replace("&-", "&").encode("ascii").decode("imap4-utf-7")
    except (UnicodeDecodeError, LookupError, AttributeError):
        try:
            return raw.decode("utf-8", "replace")
        except Exception:
            return str(raw)


def _quote(name: str) -> str:
    """
    Quote a folder name for the wire.

    A quote or a backslash in the name would otherwise end the string early and
    let the rest be read as further IMAP arguments — the mail-store equivalent
    of SQL injection.
    """
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _reject_folder_name(name: str) -> None:
    """
    Folder names PostBox refuses to create.

    Control characters and the IMAP special characters are rejected rather than
    escaped: a name containing them is never what a person meant, and refusing
    is clearer than silently transforming it.
    """
    if not name or not name.strip():
        raise MailAccessError("A folder needs a name.")
    if len(name) > 180:
        raise MailAccessError("That folder name is too long.")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise MailAccessError("That folder name contains characters that are not allowed.")
    if name.startswith(".") or "/" in name and name.strip("/") == "":
        raise MailAccessError("That folder name is not allowed.")


_FETCH_UID = re.compile(rb"UID\s+(\d+)")
_FETCH_FLAGS = re.compile(rb"FLAGS\s+\(([^)]*)\)")
_FETCH_SIZE = re.compile(rb"RFC822\.SIZE\s+(\d+)")


def _parse_fetch(data, folder: str, uid_validity: int) -> list[MessageSummary]:
    """
    Turn a batched FETCH response into summaries.

    imaplib hands back a ragged list: tuples of (metadata, literal) interleaved
    with bare bytes for the trailing `)`. Everything is keyed off the UID in the
    metadata, because that is the only stable identifier in the response.
    """
    import email
    from email.header import decode_header, make_header

    summaries: list[MessageSummary] = []

    for item in data or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        meta, literal = item[0], item[1]
        if not isinstance(meta, bytes):
            continue

        uid_match = _FETCH_UID.search(meta)
        if not uid_match:
            continue

        flags_match = _FETCH_FLAGS.search(meta)
        flags = (flags_match.group(1).decode("ascii", "replace").split()
                 if flags_match else [])
        lowered = {f.lower() for f in flags}

        size_match = _FETCH_SIZE.search(meta)

        headers = email.message_from_bytes(literal or b"")

        def header(name: str) -> str:
            value = headers.get(name, "")
            if not value:
                return ""
            try:
                return str(make_header(decode_header(value)))
            except Exception:
                # A malformed encoded-word must not take out a whole listing.
                return value

        from_name, from_address = email.utils.parseaddr(header("From"))

        summaries.append(
            MessageSummary(
                uid=int(uid_match.group(1)),
                uid_validity=uid_validity,
                folder=folder,
                message_id=header("Message-ID"),
                subject=header("Subject"),
                from_name=from_name,
                from_address=from_address,
                to=[a for _, a in email.utils.getaddresses([headers.get("To", "")]) if a],
                cc=[a for _, a in email.utils.getaddresses([headers.get("Cc", "")]) if a],
                date=header("Date"),
                size=int(size_match.group(1)) if size_match else 0,
                seen="\\seen" in lowered,
                flagged="\\flagged" in lowered,
                answered="\\answered" in lowered,
                draft="\\draft" in lowered,
                has_attachments=_structure_has_attachment(meta),
                thread_references=headers.get("References", "").split(),
                in_reply_to=header("In-Reply-To"),
            )
        )

    return summaries


def _structure_has_attachment(meta: bytes) -> bool:
    """
    Does BODYSTRUCTURE mention an attachment disposition?

    Read from the structure rather than by downloading the message, which is
    the entire reason BODYSTRUCTURE is in the FETCH. Deliberately conservative:
    it answers the question "is there a part the sender marked as an
    attachment", and an inline image is not one.
    """
    return b'"ATTACHMENT"' in meta.upper()
