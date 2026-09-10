import re

from rest_framework import serializers

from .models import Domain


class DomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = Domain
        fields = [
            "id", "domain", "status", "dns_health_score",
            "dkim_selector", "dkim_public_key",
            "mail_engine_provisioned", "mail_engine_error",
            "added_at", "verified_at",
        ]
        read_only_fields = [
            "id", "status", "dns_health_score",
            "dkim_selector", "dkim_public_key",
            "mail_engine_provisioned", "mail_engine_error",
            "added_at", "verified_at",
        ]


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
