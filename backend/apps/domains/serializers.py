import re

from rest_framework import serializers

from .models import Domain


class DomainSerializer(serializers.ModelSerializer):
    # Customer-facing names. The underlying columns keep their internal names,
    # but "mail engine" is infrastructure vocabulary and must not appear in the
    # customer API contract (DEC-011).
    mail_service_ready = serializers.BooleanField(source="mail_engine_provisioned", read_only=True)
    mail_service_message = serializers.CharField(source="mail_engine_error", read_only=True)

    # ── Ownership verification (P3) ─────────────────────────────────────────
    # The token is NOT a secret from its own tenant — the customer has to
    # publish it in public DNS. It is exposed only through this tenant-scoped
    # serializer, so another tenant can never read it and pre-empt a claim.
    ownership_verified = serializers.BooleanField(
        source="is_ownership_verified", read_only=True
    )
    verification_record_type = serializers.SerializerMethodField()
    verification_record_name = serializers.CharField(read_only=True)
    verification_record_value = serializers.CharField(read_only=True)
    verification_instructions = serializers.SerializerMethodField()

    class Meta:
        model = Domain
        fields = [
            "id", "domain", "status", "dns_health_score",
            "dkim_selector", "dkim_public_key",
            "mail_service_ready", "mail_service_message",
            "ownership_status", "ownership_verified", "ownership_verified_at",
            "verification_record_type", "verification_record_name",
            "verification_record_value", "verification_instructions",
            "verification_last_checked_at", "verification_last_error",
            "added_at", "verified_at",
        ]
        read_only_fields = fields

    def get_verification_record_type(self, obj) -> str:
        return "TXT"

    def get_verification_instructions(self, obj) -> str:
        if obj.is_ownership_verified:
            return "This domain is verified. No DNS action is needed."
        return (
            f"Add a TXT record at {obj.verification_record_name} with the exact "
            f"value shown, then run the verification check. DNS changes can take "
            f"up to an hour to propagate."
        )


# A registrable hostname: dot-separated labels of alphanumerics and hyphens,
# no leading/trailing hyphen per label, ending in an alphabetic TLD of 2+ chars.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\.[a-z]{2,63}$"
)


class DomainCreateSerializer(serializers.Serializer):
    domain = serializers.CharField(max_length=255)

    def validate_domain(self, value):
        value = value.lower().strip()
        # str.lstrip() strips a *character set*, not a prefix: "www.".lstrip on
        # "web.example.com" would eat the leading "w" and yield "eb.example.com".
        # removeprefix() is the correct operation, applied scheme-first.
        for prefix in ("https://", "http://"):
            value = value.removeprefix(prefix)
        value = value.removeprefix("www.")
        value = value.rstrip(".").strip("/")

        if not _HOSTNAME_RE.match(value):
            raise serializers.ValidationError("Enter a valid domain name (e.g. example.com).")
        if Domain.objects.filter(domain=value).exists():
            raise serializers.ValidationError("This domain is already registered.")
        return value
