"""
PostBox serializers.

Field-by-field throughout, never `fields = "__all__"`. These objects sit next
to mailbox credentials and session hashes, and an automatic serializer ships
whatever field somebody adds next.
"""
from __future__ import annotations

import re

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
    kind = serializers.CharField(read_only=True)
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
    MAX_CONDITIONS = 8
    MAX_ACTIONS = 8
    TEXT_MATCHES = {
        MailRule.Match.CONTAINS, MailRule.Match.IS,
        MailRule.Match.NOT_CONTAINS, MailRule.Match.NOT_IS,
    }
    FIELD_MATCHES = {
        MailRule.Field.FROM: TEXT_MATCHES,
        MailRule.Field.TO: TEXT_MATCHES,
        MailRule.Field.SUBJECT: TEXT_MATCHES,
        MailRule.Field.SENDER_DOMAIN: TEXT_MATCHES,
        MailRule.Field.MAILING_LIST: TEXT_MATCHES,
        MailRule.Field.BODY: {MailRule.Match.CONTAINS, MailRule.Match.NOT_CONTAINS},
        MailRule.Field.ATTACHMENT_NAME: TEXT_MATCHES,
        MailRule.Field.MESSAGE_SIZE: {MailRule.Match.OVER, MailRule.Match.UNDER},
        MailRule.Field.HAS_ATTACHMENT: {MailRule.Match.IS},
    }

    class Meta:
        model = MailRule
        fields = [
            "id", "name", "position", "enabled",
            "field", "match", "value",
            "condition_mode", "conditions",
            "action", "action_folder", "actions",
            "stop_processing", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "field": {"required": False},
            "match": {"required": False},
            "value": {"required": False},
            "action": {"required": False},
            "action_folder": {"required": False},
            "conditions": {"required": False},
            "actions": {"required": False},
        }

    @staticmethod
    def _clean_text(value, *, maximum=300, label="Value") -> str:
        if not isinstance(value, str):
            raise serializers.ValidationError(f"{label} must be text.")
        cleaned = value.strip()
        if not cleaned:
            raise serializers.ValidationError(f"{label} cannot be empty.")
        if len(cleaned) > maximum:
            raise serializers.ValidationError(f"{label} is too long.")
        return cleaned

    def _validate_conditions(self, raw) -> list[dict]:
        if not isinstance(raw, list) or not raw:
            raise serializers.ValidationError("Add at least one condition.")
        if len(raw) > self.MAX_CONDITIONS:
            raise serializers.ValidationError(
                f"A rule can have at most {self.MAX_CONDITIONS} conditions."
            )

        cleaned: list[dict] = []
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f"Condition {index} is invalid.")
            field = item.get("field")
            match = item.get("match")
            if field not in MailRule.Field.values:
                raise serializers.ValidationError(f"Condition {index} has an unknown field.")
            allowed = self.FIELD_MATCHES.get(field, set())
            if match not in allowed:
                raise serializers.ValidationError(
                    f"Condition {index} uses a match type that is not valid for that field."
                )

            value = item.get("value", "")
            if field == MailRule.Field.HAS_ATTACHMENT:
                value = str(value or "yes").strip().lower()
                if value not in {"yes", "no"}:
                    raise serializers.ValidationError(
                        f"Condition {index}: attachment value must be yes or no."
                    )
            elif field == MailRule.Field.MESSAGE_SIZE:
                try:
                    size_kb = int(str(value).strip())
                except (TypeError, ValueError):
                    raise serializers.ValidationError(
                        f"Condition {index}: message size must be a whole number of KB."
                    )
                if not 1 <= size_kb <= 10_485_760:
                    raise serializers.ValidationError(
                        f"Condition {index}: message size must be between 1 KB and 10 GB."
                    )
                value = str(size_kb)
            elif field == MailRule.Field.SENDER_DOMAIN:
                value = self._clean_text(value, maximum=253, label=f"Condition {index} domain")
                value = value.lower().lstrip("@")
                if (
                    "." not in value
                    or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
                    or ".." in value
                ):
                    raise serializers.ValidationError(
                        f"Condition {index}: enter a domain such as example.com."
                    )
            elif field == MailRule.Field.ATTACHMENT_NAME:
                value = self._clean_text(
                    value, maximum=200, label=f"Condition {index} attachment name"
                )
            else:
                value = self._clean_text(
                    value, maximum=300, label=f"Condition {index} value"
                )
            cleaned.append({"field": field, "match": match, "value": value})
        return cleaned

    def _validate_actions(self, raw) -> list[dict]:
        if not isinstance(raw, list) or not raw:
            raise serializers.ValidationError("Add at least one action.")
        if len(raw) > self.MAX_ACTIONS:
            raise serializers.ValidationError(
                f"A rule can have at most {self.MAX_ACTIONS} actions."
            )

        cleaned: list[dict] = []
        seen: set[tuple[str, str]] = set()
        terminal = 0
        for index, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                raise serializers.ValidationError(f"Action {index} is invalid.")
            action = item.get("action")
            if action in {"forward", "redirect"}:
                raise serializers.ValidationError(
                    "External forwarding is managed by your organization in MateMail Hub."
                )
            if action not in MailRule.Action.values:
                raise serializers.ValidationError(f"Action {index} is not supported.")

            folder = ""
            if action in {MailRule.Action.MOVE, MailRule.Action.COPY}:
                folder = self._clean_text(
                    item.get("folder", ""), maximum=200, label=f"Action {index} folder"
                )
            if action in {MailRule.Action.MOVE, MailRule.Action.ARCHIVE, MailRule.Action.DELETE}:
                terminal += 1

            key = (action, folder)
            if key in seen:
                raise serializers.ValidationError(f"Action {index} is duplicated.")
            seen.add(key)
            row = {"action": action}
            if folder:
                row["folder"] = folder
            cleaned.append(row)

        if terminal > 1:
            raise serializers.ValidationError(
                "Use only one final filing action: Move, Archive, or Move to Trash."
            )
        return cleaned

    def validate(self, attrs):
        instance = self.instance

        legacy_condition_changed = any(
            key in attrs for key in ("field", "match", "value")
        )
        if "conditions" in attrs:
            conditions = self._validate_conditions(attrs["conditions"])
        elif legacy_condition_changed or instance is None:
            existing = instance.normalized_conditions() if instance is not None else []
            first = {
                "field": attrs.get(
                    "field", getattr(instance, "field", MailRule.Field.FROM)
                ),
                "match": attrs.get(
                    "match", getattr(instance, "match", MailRule.Match.CONTAINS)
                ),
                "value": attrs.get("value", getattr(instance, "value", "")),
            }
            conditions = self._validate_conditions([first, *existing[1:]])
        else:
            conditions = instance.normalized_conditions()

        legacy_action_changed = any(
            key in attrs for key in ("action", "action_folder")
        )
        if "actions" in attrs:
            actions = self._validate_actions(attrs["actions"])
        elif legacy_action_changed or instance is None:
            existing_actions = instance.normalized_actions() if instance is not None else []
            action = attrs.get(
                "action", getattr(instance, "action", MailRule.Action.MOVE)
            )
            folder = attrs.get(
                "action_folder", getattr(instance, "action_folder", "")
            )
            first = {"action": action}
            if action in {MailRule.Action.MOVE, MailRule.Action.COPY}:
                first["folder"] = folder
            actions = self._validate_actions([first, *existing_actions[1:]])
        else:
            actions = instance.normalized_actions()

        mode = attrs.get(
            "condition_mode",
            getattr(instance, "condition_mode", MailRule.ConditionMode.ALL),
        )
        if mode not in MailRule.ConditionMode.values:
            raise serializers.ValidationError({"condition_mode": "Choose Match all or Match any."})
        if len(conditions) == 1:
            mode = MailRule.ConditionMode.ALL

        first_condition = conditions[0]
        first_action = actions[0]
        attrs["conditions"] = conditions
        attrs["condition_mode"] = mode
        attrs["field"] = first_condition["field"]
        attrs["match"] = first_condition["match"]
        attrs["value"] = first_condition["value"]
        attrs["actions"] = actions
        attrs["action"] = first_action["action"]
        attrs["action_folder"] = (
            first_action.get("folder", "")
            if first_action["action"] in {MailRule.Action.MOVE, MailRule.Action.COPY}
            else ""
        )
        return attrs

    def validate_value(self, value: str) -> str:
        return self._clean_text(value)


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
