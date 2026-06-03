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


class DomainCreateSerializer(serializers.Serializer):
    domain = serializers.CharField(max_length=255)

    def validate_domain(self, value):
        value = value.lower().strip().lstrip("www.").lstrip("http://").lstrip("https://")
        if "." not in value:
            raise serializers.ValidationError("Enter a valid domain name (e.g. example.com).")
        if Domain.objects.filter(domain=value).exists():
            raise serializers.ValidationError("This domain is already registered.")
        return value
