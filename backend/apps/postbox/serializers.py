"""
PostBox serializers.

Field-by-field throughout, never `fields = "__all__"`. These objects sit next
to mailbox credentials and session hashes, and an automatic serializer ships
whatever field somebody adds next.
"""
from __future__ import annotations

from rest_framework import serializers

from .models import Contact, MailRule, MailSignature, PostBoxPreference, VacationResponder


class MailboxProfileSerializer(serializers.Serializer):
    """
    The signed-in mailbox, as PostBox shows it.

    No password, no hash, no engine identifiers. `organization` is the name
    only — a mailbox user is not an administrator of it and has no business
    receiving its id, status or plan.
    """

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    quota_mb = serializers.IntegerField(read_only=True)
    organization = serializers.SerializerMethodField()
    domain = serializers.SerializerMethodField()

    def get_organization(self, mailbox) -> str:
        return mailbox.tenant.name if mailbox.tenant else ""

    def get_domain(self, mailbox) -> str:
        return mailbox.domain.domain if mailbox.domain else ""


class PreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostBoxPreference
        fields = [
            "theme", "density", "reading_pane",
            "load_remote_images", "messages_per_page", "timezone_name",
            "notify_in_app", "notify_sound", "default_identity",
        ]

    def validate_messages_per_page(self, value: int) -> int:
        # A page size is a server cost. Bounded here so a client cannot ask for
        # the whole mailbox in one FETCH.
        return max(10, min(int(value), 100))

    def validate_timezone_name(self, value: str) -> str:
        """
        A real IANA zone, checked.

        Scheduled Send interprets a local time in this zone; an unknown zone
        would silently fall back to UTC and send mail at the wrong hour.
        """
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise serializers.ValidationError("That is not a known time zone.")
        return value


class SignatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailSignature
        fields = [
            "id", "name", "html", "text", "use_for_new", "use_for_replies",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_html(self, value: str) -> str:
        """
        Sanitised on the way IN.

        Self-authored is not the same as trusted: the editor is a browser, the
        value round-trips through an API, and this HTML is injected into every
        message the mailbox sends. Cleaning at the boundary means no later
        reader of this row has to remember to do it.
        """
        from .mime import sanitize_signature

        return sanitize_signature(value or "")


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = [
            "id", "name", "email", "company", "title", "phone", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MailRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailRule
        fields = [
            "id", "name", "position", "enabled", "field", "match", "value",
            "action", "action_folder", "stop_processing",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        action = attrs.get("action", getattr(self.instance, "action", None))
        folder = attrs.get("action_folder", getattr(self.instance, "action_folder", ""))
        if action == MailRule.Action.MOVE and not (folder or "").strip():
            raise serializers.ValidationError(
                {"action_folder": "Choose a folder to move messages into."}
            )
        return attrs

    def validate_value(self, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise serializers.ValidationError("Enter something to match on.")
        if len(cleaned) > 300:
            raise serializers.ValidationError("That is too long.")
        return cleaned


class VacationSerializer(serializers.ModelSerializer):
    class Meta:
        model = VacationResponder
        fields = [
            "enabled", "subject", "message", "starts_at", "ends_at",
            "repeat_days", "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError(
                {"ends_at": "The end must be after the start."}
            )
        enabled = attrs.get("enabled", getattr(self.instance, "enabled", False))
        message = attrs.get("message", getattr(self.instance, "message", ""))
        if enabled and not (message or "").strip():
            raise serializers.ValidationError(
                {"message": "An automatic reply needs a message."}
            )
        return attrs

    def validate_repeat_days(self, value: int) -> int:
        return max(1, min(int(value), 60))
