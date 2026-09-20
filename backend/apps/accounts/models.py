import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_platform_admin", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_platform_admin = models.BooleanField(default=False)
    two_factor_enabled = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        db_table = "accounts_user"

    def __str__(self):
        return self.email


class EmailVerificationToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_verification_tokens",
    )
    token_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_email_verification_token"

    @classmethod
    def make(cls, user):
        raw = secrets.token_urlsafe(32)
        obj = cls.objects.create(
            user=user,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=24),
        )
        return raw, obj


class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="password_reset_tokens",
    )
    token_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = "accounts_password_reset_token"

    @classmethod
    def make(cls, user):
        raw = secrets.token_urlsafe(32)
        obj = cls.objects.create(
            user=user,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=1),
        )
        return raw, obj


class PlatformCodePurpose(models.TextChoices):
    LOGIN = "login", "Platform login"
    PASSWORD_RESET = "password_reset", "Platform password reset"


class PlatformEmailCode(models.Model):
    """
    A six-digit code emailed to a platform administrator, and the opaque
    challenge that carries it.

    WHY A MODEL AND NOT THE CACHE
        The tenant second factor lives in `apps.accounts.challenge`, backed by
        Redis, and that is right for it: a lost challenge there costs one
        re-login. These are the credentials for the console that can suspend
        every organization on the platform, so a Redis restart must not be able
        to silently widen or narrow the window, and an investigator needs to be
        able to ask afterwards how many codes were issued and how many failed.

    WHAT IS STORED
        Neither secret is recoverable from this table.

        `challenge_hash` is SHA-256 of the opaque token handed to the browser.
        `code_hash` is SHA-256 of the raw challenge token joined to the code.
        That second detail matters: a six-digit code is a 20-bit secret, and a
        bare digest of one falls to an offline search in milliseconds. Salting
        it with the 256-bit challenge token — which exists only in the client's
        possession and never in this table — means a stolen database yields
        nothing to search.

        `password_fingerprint` binds the challenge to the password that was
        presented. A password change voids every outstanding challenge without
        needing a reverse index, the same technique the tenant challenge uses.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_email_codes",
    )
    purpose = models.CharField(max_length=32, choices=PlatformCodePurpose.choices)
    challenge_hash = models.CharField(max_length=64, unique=True)
    code_hash = models.CharField(max_length=64)
    password_fingerprint = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    #: Long enough to read a message and type six digits, short enough that a
    #: code left on a screen is not a standing credential.
    TTL = timedelta(minutes=10)

    #: Failed code attempts before the challenge is destroyed. Five guesses
    #: against a million possibilities is a 1-in-200,000 chance.
    MAX_ATTEMPTS = 5

    class Meta:
        db_table = "accounts_platform_email_code"
        indexes = [
            models.Index(fields=["user", "purpose"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"{self.purpose} for {self.user_id}"

    # ── issuing ─────────────────────────────────────────────────────────────

    @staticmethod
    def hash_challenge(raw_challenge: str) -> str:
        return hashlib.sha256(raw_challenge.encode()).hexdigest()

    @staticmethod
    def hash_code(raw_challenge: str, code: str) -> str:
        return hashlib.sha256(f"{raw_challenge}:{code}".encode()).hexdigest()

    @staticmethod
    def password_fingerprint_for(user) -> str:
        return hashlib.sha256((user.password or "").encode()).hexdigest()[:32]

    @classmethod
    def issue(cls, user, purpose) -> tuple[str, str, "PlatformEmailCode"]:
        """
        Create a challenge and its code. Returns (raw_challenge, raw_code, row).

        Any outstanding challenge of the same purpose for this user is consumed
        first. Two live login codes would mean an attacker who triggers a resend
        does not invalidate the one they are trying to guess.
        """
        cls.objects.filter(
            user=user, purpose=purpose, consumed_at__isnull=True
        ).update(consumed_at=timezone.now())

        raw_challenge = secrets.token_urlsafe(32)
        # randbelow, not randint: a uniform draw from a CSPRNG. Zero-padded so
        # every code is six characters and none of them leaks its magnitude.
        code = f"{secrets.randbelow(1_000_000):06d}"

        row = cls.objects.create(
            user=user,
            purpose=purpose,
            challenge_hash=cls.hash_challenge(raw_challenge),
            code_hash=cls.hash_code(raw_challenge, code),
            password_fingerprint=cls.password_fingerprint_for(user),
            expires_at=timezone.now() + cls.TTL,
        )
        return raw_challenge, code, row

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def matches(self, raw_challenge: str, code: str) -> bool:
        """Constant-time comparison of the salted digest."""
        return secrets.compare_digest(
            self.code_hash, self.hash_code(raw_challenge, code)
        )


class TwoFactorSetup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="two_factor_setup",
    )
    totp_secret = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_two_factor_setup"


class TwoFactorBackupCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="backup_codes",
    )
    code_hash = models.CharField(max_length=64)
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_two_factor_backup_code"
