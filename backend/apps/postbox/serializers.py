"""
PostBox serializers.

Field-by-field throughout, never `fields = "__all__"`. These objects sit next
to mailbox credentials and session hashes, and an automatic serializer ships
whatever field somebody adds next.
"""
from __future__ import annotations

from django.db import transaction
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
            "theme", "density", "reading_pane", "list_view", "reader_view",
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
    """
    A signature, with its kind stated.

    The image bytes are deliberately NOT a field here. They are written by a
    dedicated upload endpoint that can check magic bytes and dimensions, and
    read by an endpoint that serves them with a real content type — putting
    them in this JSON would mean base64 on every list response and no place
    to validate the file itself.
    """

    #: What the client needs to render a preview and an <img>, without ever
    #: receiving the bytes.
    has_image = serializers.SerializerMethodField()
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = MailSignature
        fields = [
            "id", "name", "kind", "html", "text",
            "image_alt", "image_content_type", "has_image", "image_url",
            "use_for_new", "use_for_replies",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "image_content_type", "has_image", "image_url",
            "created_at", "updated_at",
        ]

    def get_has_image(self, signature) -> bool:
        return bool(signature.image_data)

    def get_image_url(self, signature) -> str:
        """Relative, so it follows whichever hostname PostBox is served on."""
        if not signature.image_data:
            return ""
        return f"/api/postbox/signatures/{signature.id}/image/"

    def validate_html(self, value: str) -> str:
        """
        Sanitised on the way IN.

        Self-authored is not the same as trusted: the editor is a browser, the
        value round-trips through an API, and this HTML is injected into every
        message the mailbox sends. Cleaning at the boundary means no later
        reader of this row has to remember to do it.

        This also strips a pasted document wrapper, so <title> and <style>
        cannot arrive as visible words in somebody's inbox.
        """
        from .mime import sanitize_signature

        return sanitize_signature(value or "")

    def validate(self, attrs):
        """
        Per-kind rules, and the generated plain-text fallback.

        RESOLVING `kind`, in order:

          1. what the caller sent — including `text` alongside `html`, which
             is a contradiction and gets the answer the caller asked for;
          2. non-empty `html` with no `kind` — an API client written before
             this field existed. Defaulting those to text would throw away
             HTML the caller explicitly supplied, which is exactly the kind
             of silent content loss this whole change exists to end;
          3. the instance's own kind, so a PATCH that only toggles
             `use_for_new` does not revalidate an HTML signature as text;
          4. text.
        """
        from .mime import html_to_text
        from .models import SignatureKind

        if attrs.get("kind"):
            kind = attrs["kind"]
        elif attrs.get("html"):
            kind = SignatureKind.HTML
        else:
            kind = getattr(self.instance, "kind", SignatureKind.TEXT)

        # Written back, so an inferred kind is persisted rather than being
        # re-inferred differently by the next reader of the row.
        attrs["kind"] = kind

        if kind == SignatureKind.HTML:
            html = attrs.get("html", getattr(self.instance, "html", ""))
            # Derived, never demanded. Asking somebody to maintain a second
            # copy of their own signature guarantees the two drift, and the
            # one that drifts is the one nobody looks at.
            # Only when the caller did not supply one: an explicit text is
            # the optional advanced override, and silently replacing it
            # would make the field a lie.
            if not attrs.get("text"):
                attrs["text"] = html_to_text(html)

        elif kind == SignatureKind.IMAGE:
            alt = attrs.get("image_alt", getattr(self.instance, "image_alt", ""))
            if not (alt or "").strip():
                raise serializers.ValidationError({
                    "image_alt": "Describe the image, so it still reads when images are blocked.",
                })
            # An image signature's words ARE its alt text.
            attrs["text"] = alt
            attrs["html"] = ""

        else:
            # Plain text is plain text. Anything in `html` would be a second
            # source of truth nobody edits.
            attrs["html"] = ""

        return attrs

    @staticmethod
    def _clear_other_defaults(mailbox, instance, validated_data) -> None:
        """
        A mailbox has at most one default signature for each compose mode.

        Lock the mailbox row, not merely existing signature rows. That also
        serializes two concurrent first-signature creates, where there would be
        no signature row to lock yet. The partial unique constraints on the
        model are the final database-level guard.
        """
        type(mailbox).objects.select_for_update().get(pk=mailbox.pk)

        siblings = MailSignature.objects.for_mailbox(mailbox)
        if instance is not None:
            siblings = siblings.exclude(pk=instance.pk)

        if validated_data.get("use_for_new") is True:
            siblings.filter(use_for_new=True).update(use_for_new=False)

        if validated_data.get("use_for_replies") is True:
            siblings.filter(use_for_replies=True).update(use_for_replies=False)

    def create(self, validated_data):
        mailbox = validated_data["mailbox"]
        with transaction.atomic():
            self._clear_other_defaults(mailbox, None, validated_data)
            return super().create(validated_data)

    def update(self, instance, validated_data):
        with transaction.atomic():
            self._clear_other_defaults(instance.mailbox, instance, validated_data)
            return super().update(instance, validated_data)


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
