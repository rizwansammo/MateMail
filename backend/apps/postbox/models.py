"""
PostBox persistent state.

WHAT LIVES HERE AND WHAT DOES NOT
    Dovecot owns the mail. There is no Django model for a message, a folder or
    a flag, and there must never be one: duplicating the mail store into
    Postgres creates two answers to "is this read?" and guarantees they will
    disagree. Everything below is application state that IMAP has no place to
    put — a browser session, a signature, a contact, a rule, a scheduled job.

    The single exception is `ScheduledMessage`, and even that stores only
    *metadata*. The message itself is a real RFC 5322 message sitting in a real
    IMAP folder; this table records when to send it and whether it has been
    sent.

SCOPING
    Every row belongs to exactly one `Mailbox`. Not to a tenant and not to a
    User: a MateMail Workspace account and a mailbox are different identities,
    and an organization administrator having a Workspace login says nothing
    about whose mail they may read. Queries are scoped by mailbox, enforced by
    the session, and the security tests prove cross-mailbox access fails.
"""
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone


class MailboxScopedQuerySet(models.QuerySet):
    """`for_mailbox` reads as an intent, which makes an unscoped query visible."""

    def for_mailbox(self, mailbox):
        return self.filter(mailbox=mailbox)


class PostBoxSession(models.Model):
    """
    One signed-in PostBox browser.

    NO AUTHENTICATION SECRET IS STORED HERE.

    The browser holds an opaque token in a host-scoped HttpOnly cookie; this
    table holds only its SHA-256. A stolen database therefore yields no usable
    session, and a row can be revoked without knowing what the browser has.

    The mailbox password is not here either — not hashed, not encrypted, not
    at all. PostBox verifies it once against Dovecot at sign-in and then reads
    the mailbox through the master identity, bound to this row. That is the
    whole reason this table exists rather than a cookie carrying claims.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_sessions"
    )

    #: SHA-256 of the opaque cookie value. Unique so a token cannot address two
    #: rows, and indexed because it is the lookup on every single request.
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    #: Recorded so an operator can recognise their own sessions in the security
    #: page. Truncated on write; a full user-agent string is a fingerprint and
    #: adds nothing an operator can act on.
    user_agent = models.CharField(max_length=200, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    #: "Remember me" only changes the lifetime, never the security properties.
    remembered = models.BooleanField(default=False)

    SESSION_TTL = timedelta(hours=12)
    REMEMBERED_TTL = timedelta(days=30)

    #: How stale `last_seen_at` may get before it is written again. Every
    #: request touching the row would turn a read-mostly table into a write on
    #: every API call, for a field nobody reads to the minute.
    LAST_SEEN_RESOLUTION = timedelta(minutes=5)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_session"
        ordering = ["-last_seen_at"]
        indexes = [
            models.Index(fields=["mailbox", "-last_seen_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"PostBox session for {self.mailbox_id}"

    # ── lifecycle ───────────────────────────────────────────────────────────

    @staticmethod
    def hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, mailbox, *, remembered=False, user_agent="", ip_address=None):
        """Create a session and return (raw_token, row). The raw token is never stored."""
        raw = secrets.token_urlsafe(48)
        ttl = cls.REMEMBERED_TTL if remembered else cls.SESSION_TTL
        row = cls.objects.create(
            mailbox=mailbox,
            token_hash=cls.hash_token(raw),
            expires_at=timezone.now() + ttl,
            user_agent=(user_agent or "")[:200],
            ip_address=ip_address,
            remembered=remembered,
        )
        return raw, row

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and timezone.now() < self.expires_at

    def touch(self) -> None:
        """Record activity, at most once per LAST_SEEN_RESOLUTION."""
        now = timezone.now()
        if now - self.last_seen_at >= self.LAST_SEEN_RESOLUTION:
            self.last_seen_at = now
            self.save(update_fields=["last_seen_at"])

    def revoke(self) -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])


class PostBoxPreference(models.Model):
    """
    Mailbox preferences that should follow the person between devices.

    The split is deliberate. Theme, density and reading pane live here because
    somebody who sets dark mode on their laptop expects dark mode on their
    phone. Genuinely device-local state — which folders are expanded in the
    sidebar, the width of a pane — stays in the browser, because syncing it
    would make two devices fight.
    """

    class Theme(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"
        SYSTEM = "system", "System"

    class Density(models.TextChoices):
        COMFORTABLE = "comfortable", "Comfortable"
        COMPACT = "compact", "Compact"

    class ReadingPane(models.TextChoices):
        RIGHT = "right", "Right"
        BOTTOM = "bottom", "Bottom"
        OFF = "off", "Off"

    mailbox = models.OneToOneField(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_preference"
    )

    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)
    density = models.CharField(
        max_length=12, choices=Density.choices, default=Density.COMFORTABLE
    )
    reading_pane = models.CharField(
        max_length=8, choices=ReadingPane.choices, default=ReadingPane.RIGHT
    )
    #: Blocked by default. A remote image in an email is a tracking pixel until
    #: proven otherwise, and loading one tells the sender the address is live
    #: and reveals the reader's IP.
    load_remote_images = models.BooleanField(default=False)

    messages_per_page = models.PositiveSmallIntegerField(default=50)

    #: IANA name. Used to render dates and to interpret a Scheduled Send time,
    #: which is the one place getting it wrong sends mail at the wrong hour.
    timezone_name = models.CharField(max_length=64, default="UTC")

    #: In-browser notifications while PostBox is open. Deliberately not web
    #: push: MateMail runs no push service, and a toggle that implied one would
    #: be a promise the product does not keep.
    notify_in_app = models.BooleanField(default=True)
    notify_sound = models.BooleanField(default=False)

    default_identity = models.EmailField(blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "postbox_preference"

    def __str__(self):
        return f"Preferences for {self.mailbox_id}"


class SignatureKind(models.TextChoices):
    """
    What a signature IS, stated rather than guessed.

    Before this field there was one content box and two columns, `html` and
    `text`, and nothing said which the person had filled in. The UI bound to
    `text`, so a pasted HTML signature was stored as plain text and appended
    verbatim to a plain-text body — and the recipient read the markup. The
    columns were never the problem; the missing declaration was.
    """

    TEXT = "text", "Plain text"
    HTML = "html", "HTML"
    IMAGE = "image", "Image"


class MailSignature(models.Model):
    """
    A signature, stored sanitised.

    Sanitised on WRITE, not on render. A signature is the one piece of HTML in
    PostBox authored by the mailbox owner, and storing it raw would mean every
    future reader of this row — an export, a different client, a migration —
    has to remember to sanitise it again. Cleaning once at the boundary is the
    only version that stays true.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_signatures"
    )

    name = models.CharField(max_length=100)

    #: Which of the fields below is authoritative. Everything that renders a
    #: signature branches on this rather than on which column looks non-empty.
    kind = models.CharField(
        max_length=8, choices=SignatureKind.choices, default=SignatureKind.TEXT
    )

    html = models.TextField(blank=True, default="")
    #: Kept alongside the HTML so a plain-text alternative never has to be
    #: derived at send time, when getting it wrong means an unreadable message.
    #: For an HTML signature it is generated from the sanitised HTML on save,
    #: so nobody has to maintain two copies of the same thing.
    text = models.TextField(blank=True, default="")

    #: An image signature's bytes, in the row rather than on disk.
    #:
    #: These are small by construction (see POSTBOX_SIGNATURE_IMAGE_KB) and
    #: keeping them here means no media root, no per-file permissions, and no
    #: filesystem path that could ever be reflected to a caller. It also means
    #: a database backup contains the whole signature, which is the behaviour
    #: somebody restoring a mailbox expects.
    image_data = models.BinaryField(blank=True, default=bytes)
    image_content_type = models.CharField(max_length=64, blank=True, default="")
    image_filename = models.CharField(max_length=255, blank=True, default="")
    #: Required for an image signature. A signature that is only an image is
    #: invisible to a screen reader and to anyone with images turned off,
    #: which is most corporate mail clients by default.
    image_alt = models.CharField(max_length=200, blank=True, default="")

    use_for_new = models.BooleanField(default=False)
    use_for_replies = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_signature"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "name"], name="postbox_signature_unique_name"
            ),
            models.UniqueConstraint(
                fields=["mailbox"],
                condition=models.Q(use_for_new=True),
                name="postbox_signature_one_default_new",
            ),
            models.UniqueConstraint(
                fields=["mailbox"],
                condition=models.Q(use_for_replies=True),
                name="postbox_signature_one_default_replies",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.mailbox_id})"


class Contact(models.Model):
    """A mailbox's own address book. Never shared, never organization-wide."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_contacts"
    )

    name = models.CharField(max_length=200, blank=True, default="")
    email = models.EmailField()
    company = models.CharField(max_length=200, blank=True, default="")
    title = models.CharField(max_length=200, blank=True, default="")
    phone = models.CharField(max_length=60, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_contact"
        ordering = ["name", "email"]
        constraints = [
            # One row per address per mailbox. Two mailboxes may of course both
            # know the same person.
            models.UniqueConstraint(
                fields=["mailbox", "email"], name="postbox_contact_unique_email"
            )
        ]
        indexes = [
            models.Index(fields=["mailbox", "email"]),
            models.Index(fields=["mailbox", "name"]),
        ]

    def __str__(self):
        return self.email


class MailRule(models.Model):
    """
    A mailbox-user filter, stored STRUCTURED and compiled to Sieve.

    The browser never sends Sieve. It sends a condition and an action from the
    small vocabulary below, and `apps.postbox.sieve` renders the script. That
    is the whole security design: raw Sieve from a browser would be arbitrary
    code running as the mail server, with `redirect` available to forward a
    customer's mail anywhere.

    Order matters — Sieve evaluates top to bottom and `stop` ends processing —
    so `position` is part of the data rather than an incidental ordering.
    """

    class Field(models.TextChoices):
        FROM = "from", "From"
        TO = "to", "To or Cc"
        SUBJECT = "subject", "Subject"

    class Match(models.TextChoices):
        CONTAINS = "contains", "contains"
        IS = "is", "is exactly"

    class Action(models.TextChoices):
        MOVE = "move", "Move to folder"
        STAR = "star", "Star"
        MARK_READ = "mark_read", "Mark as read"
        DELETE = "delete", "Move to Trash"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_rules"
    )

    name = models.CharField(max_length=120)
    position = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    field = models.CharField(max_length=16, choices=Field.choices)
    match = models.CharField(max_length=16, choices=Match.choices, default=Match.CONTAINS)
    value = models.CharField(max_length=300)

    action = models.CharField(max_length=16, choices=Action.choices)
    #: Only meaningful for MOVE. Validated against the mailbox's real folder
    #: list before compilation — a rule naming a folder that does not exist is
    #: mail silently going nowhere.
    action_folder = models.CharField(max_length=200, blank=True, default="")

    #: Stop evaluating further rules when this one matches.
    stop_processing = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_rule"
        ordering = ["position", "created_at"]
        indexes = [models.Index(fields=["mailbox", "position"])]

    def __str__(self):
        return f"{self.name} ({self.mailbox_id})"


class VacationResponder(models.Model):
    """
    Auto-reply, compiled into the same Sieve script as the rules.

    Sieve's `vacation` is used rather than anything MateMail writes, because it
    already implements the parts that are easy to get catastrophically wrong:
    it replies at most once per sender per period, it does not reply to
    bulk/list mail, and it does not reply to a bounce. A hand-rolled responder
    that missed any of those becomes a mail loop.
    """

    mailbox = models.OneToOneField(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_vacation"
    )

    enabled = models.BooleanField(default=False)
    subject = models.CharField(max_length=200, blank=True, default="")
    message = models.TextField(blank=True, default="")

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    #: Sieve's own suppression window: the same sender gets at most one reply
    #: in this many days.
    repeat_days = models.PositiveSmallIntegerField(default=7)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "postbox_vacation"

    def __str__(self):
        return f"Vacation for {self.mailbox_id}"

    @property
    def is_active_now(self) -> bool:
        if not self.enabled:
            return False
        now = timezone.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


class ScheduledMessage(models.Model):
    """
    Metadata for a message that is waiting to be sent.

    THE MESSAGE IS NOT HERE. It is a real RFC 5322 message in the mailbox's
    Scheduled IMAP folder, addressed by (uid_validity, uid). This table records
    when to send it and what happened — which keeps the mail in the mail store
    where the rest of it lives, and keeps Postgres from becoming a second
    mail spool.

    IDEMPOTENCY
        Celery retries. A task that simply "sends the message" would send it
        twice the first time a worker died between `smtp.send` and the status
        update. `claim()` performs a conditional UPDATE from PENDING to SENDING
        and reports whether it won; a retry that did not win does nothing. The
        `sent_message_id` column then records exactly which message was
        accepted, so a human can tell the two apart afterwards.
    """

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_scheduled"
    )

    #: Where the message actually is. UIDVALIDITY is stored with the UID
    #: because a UID alone is meaningless if the folder is recreated — that is
    #: precisely what UIDVALIDITY exists to tell you.
    folder = models.CharField(max_length=200, default="Scheduled")
    uid_validity = models.BigIntegerField()
    uid = models.BigIntegerField()

    #: Denormalised for the UI, so listing scheduled mail does not require an
    #: IMAP round trip per row. Never the body.
    subject = models.CharField(max_length=998, blank=True, default="")
    recipients = models.TextField(blank=True, default="")

    scheduled_at = models.DateTimeField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)

    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    #: The Message-ID actually accepted by Postfix. The proof of what was sent.
    sent_message_id = models.CharField(max_length=998, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_scheduled_message"
        ordering = ["scheduled_at"]
        indexes = [
            models.Index(fields=["state", "scheduled_at"]),
            models.Index(fields=["mailbox", "-scheduled_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "folder", "uid_validity", "uid"],
                name="postbox_scheduled_unique_message",
            )
        ]

    def __str__(self):
        return f"Scheduled {self.id} for {self.scheduled_at:%Y-%m-%d %H:%M}"

    def claim(self) -> bool:
        """
        Atomically take ownership of this send. True exactly once.

        The conditional UPDATE is the whole idempotency mechanism: two workers
        racing on the same row produce one winner, and a retry after a crash
        finds the row already in SENDING and declines.
        """
        claimed = (
            ScheduledMessage.objects.filter(pk=self.pk, state=self.State.PENDING)
            .update(state=self.State.SENDING, attempts=models.F("attempts") + 1)
        )
        if claimed:
            self.refresh_from_db(fields=["state", "attempts"])
        return bool(claimed)
