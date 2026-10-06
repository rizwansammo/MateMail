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
from django.db.models.functions import Lower
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
    # Optional mailbox currently being viewed through this personal session.
    # The authenticated identity remains `mailbox`; only an explicitly
    # authorised TeamBox may be stored here.
    active_mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_postbox_sessions",
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
        # Push registrations follow their session and a revoked one can never
        # use them again, so their provider tokens go now rather than when the
        # session row is eventually pruned.
        self.push_devices.all().delete()


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
        EXTRA_COMPACT = "extra_compact", "Extra Compact"

    class ReadingPane(models.TextChoices):
        RIGHT = "right", "Right"
        BOTTOM = "bottom", "Bottom"
        OFF = "off", "Off"

    mailbox = models.OneToOneField(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_preference"
    )

    theme = models.CharField(max_length=10, choices=Theme.choices, default=Theme.SYSTEM)
    density = models.CharField(
        max_length=13, choices=Density.choices, default=Density.COMFORTABLE
    )
    reading_pane = models.CharField(
        max_length=8, choices=ReadingPane.choices, default=ReadingPane.RIGHT
    )
    # Mailbox-wide choices; list grouping and the opened reader are independent.
    list_view = models.CharField(
        max_length=13, choices=[("conversations", "Conversations"), ("messages", "Messages")],
        default="conversations",
    )
    reader_view = models.CharField(
        max_length=6, choices=[("thread", "Conversation"), ("single", "Single message")],
        default="thread",
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


class RemoteImageSenderTrust(models.Model):
    """
    A mailbox-scoped decision to load remote images from one From address.

    This is deliberately separate from the global `load_remote_images`
    preference. Clicking "Display images" never writes here; only the explicit
    "Always display images from ..." action creates a row.
    """

    mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="remote_image_sender_trusts",
    )
    sender = models.EmailField(max_length=254)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_remote_image_sender_trust"
        ordering = ["sender"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "sender"],
                name="postbox_remote_image_sender_trust_unique",
            )
        ]

    def save(self, *args, **kwargs):
        self.sender = (self.sender or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sender} for {self.mailbox_id}"


class MessageMoveProvenance(models.Model):
    """
    Where a message lived before PostBox moved it to Trash or Spam.

    IMAP deliberately has no "previous folder" concept, and a MOVE may assign
    a different UID in the destination folder.  The record is therefore keyed
    by a stable digest of the RFC 5322 message identity rather than by UID.
    Dovecot remains the source of truth for the message itself; this row stores
    only application state IMAP cannot represent.
    """

    mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="postbox_move_provenance",
    )
    message_key = models.CharField(max_length=80)
    original_folder = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_message_move_provenance"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "message_key"],
                name="postbox_message_move_provenance_unique",
            )
        ]

    def __str__(self):
        return f"{self.message_key} -> {self.original_folder} for {self.mailbox_id}"


class MessageOrigin(models.Model):
    """
    Stable semantic origin for messages that PostBox itself moved out of Sent.

    IMAP folders describe where a message is now, not what kind of message it
    originally was. Keeping this tiny content-free marker lets a sent message
    live in a custom archive folder while PostBox still renders it as outgoing
    and can safely offer "Move back to Sent" only for that message.
    """

    class Role(models.TextChoices):
        SENT = "sent", "Sent"

    mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.CASCADE,
        related_name="postbox_message_origins",
    )
    message_key = models.CharField(max_length=80)
    origin_role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_message_origin"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "message_key"],
                name="postbox_message_origin_unique",
            )
        ]

    def __str__(self):
        return f"{self.message_key} ({self.origin_role}) for {self.mailbox_id}"


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

    def normalized_conditions(self) -> list[dict]:
        if isinstance(self.conditions, list) and self.conditions:
            return [item for item in self.conditions if isinstance(item, dict)]
        return [{"field": self.field, "match": self.match, "value": self.value}]

    def normalized_actions(self) -> list[dict]:
        if isinstance(self.actions, list) and self.actions:
            return [item for item in self.actions if isinstance(item, dict)]
        item = {"action": self.action}
        if self.action_folder:
            item["folder"] = self.action_folder
        return [item]

    def references_folder(self, name: str) -> bool:
        return any(
            item.get("action") in {self.Action.MOVE, self.Action.COPY}
            and item.get("folder") == name
            for item in self.normalized_actions()
        )

    def retarget_folder(self, old_name: str, new_name: str) -> bool:
        changed = False
        updated = []
        for item in self.normalized_actions():
            item = dict(item)
            if item.get("action") in {self.Action.MOVE, self.Action.COPY} and item.get("folder") == old_name:
                item["folder"] = new_name
                changed = True
            updated.append(item)
        if not changed:
            return False
        self.actions = updated
        first = updated[0]
        self.action = first.get("action", self.action)
        self.action_folder = first.get("folder", "") if self.action in {self.Action.MOVE, self.Action.COPY} else ""
        return True

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
        SENDER_DOMAIN = "sender_domain", "Sender domain"
        MAILING_LIST = "mailing_list", "Mailing list"
        BODY = "body", "Message body"
        MESSAGE_SIZE = "message_size", "Message size"
        HAS_ATTACHMENT = "has_attachment", "Has attachment"
        ATTACHMENT_NAME = "attachment_name", "Attachment name"

    class Match(models.TextChoices):
        CONTAINS = "contains", "contains"
        IS = "is", "is exactly"
        NOT_CONTAINS = "not_contains", "does not contain"
        NOT_IS = "not_is", "is not exactly"
        OVER = "over", "is over"
        UNDER = "under", "is under"

    class Action(models.TextChoices):
        MOVE = "move", "Move to folder"
        COPY = "copy", "Copy to folder"
        ARCHIVE = "archive", "Archive"
        STAR = "star", "Star"
        MARK_READ = "mark_read", "Mark as read"
        MARK_UNREAD = "mark_unread", "Mark as unread"
        DELETE = "delete", "Move to Trash"

    class ConditionMode(models.TextChoices):
        ALL = "all", "Match all conditions"
        ANY = "any", "Match any condition"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_rules"
    )

    name = models.CharField(max_length=120)
    position = models.PositiveSmallIntegerField(default=0)
    enabled = models.BooleanField(default=True)

    # Legacy/primary condition fields are retained for backwards-compatible
    # clients and are mirrored from the first structured condition.
    field = models.CharField(max_length=16, choices=Field.choices)
    match = models.CharField(max_length=16, choices=Match.choices, default=Match.CONTAINS)
    value = models.CharField(max_length=300)

    condition_mode = models.CharField(
        max_length=3, choices=ConditionMode.choices, default=ConditionMode.ALL,
    )
    # Structured, bounded data only. Raw Sieve is never accepted.
    # [{"field": "from", "match": "is", "value": "person@example.com"}, ...]
    conditions = models.JSONField(default=list, blank=True)

    # Legacy/primary action mirrors the first structured action.
    action = models.CharField(max_length=16, choices=Action.choices)
    # [{"action": "star"}, {"action": "move", "folder": "Finance"}, ...]
    actions = models.JSONField(default=list, blank=True)
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

    def normalized_conditions(self) -> list[dict]:
        if isinstance(self.conditions, list) and self.conditions:
            return [item for item in self.conditions if isinstance(item, dict)]
        return [{"field": self.field, "match": self.match, "value": self.value}]

    def normalized_actions(self) -> list[dict]:
        if isinstance(self.actions, list) and self.actions:
            return [item for item in self.actions if isinstance(item, dict)]
        item = {"action": self.action}
        if self.action_folder:
            item["folder"] = self.action_folder
        return [item]

    def references_folder(self, name: str) -> bool:
        return any(
            item.get("action") in {self.Action.MOVE, self.Action.COPY}
            and item.get("folder") == name
            for item in self.normalized_actions()
        )

    def retarget_folder(self, old_name: str, new_name: str) -> bool:
        changed = False
        updated = []
        for item in self.normalized_actions():
            item = dict(item)
            if (
                item.get("action") in {self.Action.MOVE, self.Action.COPY}
                and item.get("folder") == old_name
            ):
                item["folder"] = new_name
                changed = True
            updated.append(item)
        if not changed:
            return False

        self.actions = updated
        first = updated[0]
        self.action = first.get("action", self.action)
        self.action_folder = (
            first.get("folder", "")
            if self.action in {self.Action.MOVE, self.Action.COPY}
            else ""
        )
        return True

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
    # The personal mailbox that authenticated SMTP submission when this was
    # scheduled. For ordinary personal-mailbox sends it may be NULL and falls
    # back to `mailbox`. TeamBox scheduled sends always set it explicitly so
    # the worker never attempts to authenticate as the passwordless TeamBox.
    submission_mailbox = models.ForeignKey(
        "mailboxes.Mailbox",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="postbox_scheduled_submissions",
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


# ── Native PostBox push (remote new-mail notifications) ─────────────────────
#
# Two tables, both application metadata. Dovecot stays the only mail store: an
# event row names a saved message by folder, UIDVALIDITY and UID and holds
# nothing of it — no sender, subject, body or attachment name. See
# docs/POSTBOX_REMOTE_PUSH.md.


class PushPlatform(models.TextChoices):
    ANDROID = "android", "Android"
    WINDOWS = "windows", "Windows"


class PushProvider(models.TextChoices):
    FCM = "fcm", "Firebase Cloud Messaging"
    WNS = "wns", "Windows Push Notification Services"


class PushTokenType(models.TextChoices):
    """What the provider identifier is. FCM accepts two kinds (see providers)."""

    REGISTRATION_TOKEN = "registration_token", "FCM registration token"
    FID = "fid", "Firebase installation ID"
    CHANNEL_URI = "channel_uri", "WNS channel URI"


class PostBoxPushDevice(models.Model):
    """
    One PostBox installation's push registration for ONE mailbox.

    OWNERSHIP
        A registration belongs to a mailbox AND to the PostBox session that
        created it. One installation signed in to three mailboxes has three
        rows, one per mailbox, each with that mailbox's own session. Push goes
        only to a row whose session is still active, for a mailbox that may
        still sign in (checked at dispatch and again at send). Revoking the
        session - signing out, "sign out everywhere", a password change -
        deletes its rows at once (`PostBoxSession.revoke`,
        `revoke_other_sessions`). An expired session's rows are never selected
        and go with the session when it is pruned, a week after expiry.

    THE TOKEN
        An FCM token or a WNS channel URI is what lets a provider reach this
        installation. It is not a MateMail credential, but it is operationally
        sensitive, so it is written once and never read back: no API response,
        log line, error or push payload contains it. It is stored in the clear
        because the provider needs it verbatim and MateMail has no
        general-purpose encryption for such values (the DKIM keystore is scoped
        to DKIM keys by design); the residual exposure is the database itself.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_push_devices"
    )
    session = models.ForeignKey(
        PostBoxSession, on_delete=models.CASCADE, related_name="push_devices"
    )

    #: Generated by the PostBox app once per installation. The same value may
    #: appear under several mailboxes — that is one device, several accounts.
    installation_id = models.UUIDField()
    platform = models.CharField(max_length=10, choices=PushPlatform.choices)
    provider = models.CharField(max_length=10, choices=PushProvider.choices)
    token_type = models.CharField(max_length=20, choices=PushTokenType.choices)
    token = models.TextField()

    enabled = models.BooleanField(default=True)
    #: Why a provider made this registration unusable: a safe code, never text
    #: from the provider.
    disabled_reason = models.CharField(max_length=32, blank=True, default="")
    disabled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    #: The last time the app registered (it re-registers on every launch).
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=32, blank=True, default="")

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_push_device"
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "installation_id", "provider"],
                name="postbox_push_device_unique_installation",
            )
        ]
        indexes = [models.Index(fields=["mailbox", "enabled"])]

    def __str__(self):
        return f"{self.platform}/{self.provider} push device {self.id}"


class PostBoxPushEvent(models.Model):
    """
    One committed delivery, as the Native Engine reported it.

    IDENTITY ONLY. The mailbox, folder, UIDVALIDITY and UID of a message
    Dovecot saved — never its content. `event_id` is derived by the engine from
    exactly those four values, so the same delivery reported twice is the same
    row, and the same id travels in every push so the app can drop repeats.

    STATE
        PENDING until a worker claims it, with a conditional UPDATE, and fans it
        out to the mailbox's active devices (DISPATCHED). A beat sweep re-queues
        a PENDING row Celery never received, and marks one too old to be worth
        announcing EXPIRED. Rows are pruned after a week.
    """

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"
        EXPIRED = "expired", "Expired"

    class Kind(models.TextChoices):
        NEW_MAIL = "new_mail", "New mail"

    event_id = models.UUIDField(primary_key=True, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_push_events"
    )
    event_type = models.CharField(max_length=16, choices=Kind.choices, default=Kind.NEW_MAIL)
    folder = models.CharField(max_length=255)
    #: Null only for an event that names no message ("this mailbox changed").
    uid_validity = models.BigIntegerField(null=True, blank=True)
    uid = models.BigIntegerField(null=True, blank=True)

    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    #: How many active devices it was fanned out to, for diagnostics.
    devices = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_push_event"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["state", "created_at"])]

    def __str__(self):
        return f"Push event {self.event_id} ({self.state})"

    def claim(self) -> bool:
        """Take this event from PENDING to DISPATCHED. True exactly once."""
        return bool(
            PostBoxPushEvent.objects.filter(pk=self.pk, state=self.State.PENDING).update(
                state=self.State.DISPATCHED, dispatched_at=timezone.now()
            )
        )


class FolderAppearance(models.Model):
    """PostBox-only mailbox-scoped visual metadata for real IMAP folders.

    Folder names and message contents remain owned by IMAP/Dovecot.
    """

    id = models.BigAutoField(primary_key=True)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE,
        related_name="postbox_folder_appearances",
    )
    name = models.CharField(max_length=255)
    color = models.CharField(max_length=7, default="#2563eb")

    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_folder_appearance"
        constraints = [
            models.UniqueConstraint(
                fields=["mailbox", "name"],
                name="postbox_folder_appearance_unique",
            ),
        ]


class MailLabel(models.Model):
    """Mailbox-private virtual label; never creates another IMAP message."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mailbox = models.ForeignKey(
        "mailboxes.Mailbox", on_delete=models.CASCADE, related_name="postbox_labels"
    )
    name = models.CharField(max_length=80)
    color = models.CharField(max_length=7, default="#9333ea")
    created_at = models.DateTimeField(auto_now_add=True)
    objects = MailboxScopedQuerySet.as_manager()

    class Meta:
        db_table = "postbox_label"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), "mailbox",
                name="postbox_label_mailbox_name_ci",
            )
        ]


class MessageLabel(models.Model):
    """Stable message identity, independent of an IMAP folder/UID after MOVE."""

    id = models.BigAutoField(primary_key=True)
    label = models.ForeignKey(
        MailLabel, on_delete=models.CASCADE, related_name="assignments"
    )
    message_key = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "postbox_message_label"
        constraints = [
            models.UniqueConstraint(
                fields=["label", "message_key"],
                name="postbox_message_label_unique",
            )
        ]
        indexes = [models.Index(fields=["message_key"], name="pb_label_msg_key_idx")]
