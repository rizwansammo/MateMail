from rest_framework import serializers

from apps.domains.models import DomainOwnership

from .custom_hosts import (
    CustomHostnameValueError,
    cname_target,
    validate_customer_hostname,
)
from .models import CustomHostname, CustomHostnameSurface


class CustomHostnameSerializer(serializers.ModelSerializer):
    cname_record_type = serializers.SerializerMethodField()
    cname_target = serializers.SerializerMethodField()
    cname_zone = serializers.SerializerMethodField()
    cname_host = serializers.SerializerMethodField()
    setup_instructions = serializers.SerializerMethodField()

    class Meta:
        model = CustomHostname
        fields = [
            "id",
            "hostname",
            "surface",
            "dns_status",
            "dns_verified_at",
            "dns_last_checked_at",
            "provisioning_status",
            "certificate_status",
            "last_error",
            "activated_at",
            "deactivated_at",
            "cname_record_type",
            "cname_target",
            "cname_zone",
            "cname_host",
            "setup_instructions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_cname_record_type(self, obj) -> str:
        return "CNAME"

    def get_cname_target(self, obj) -> str:
        return cname_target()

    def get_cname_zone(self, obj) -> str | None:
        """Use only a verified DNS zone for this tenant, never a guessed TLD."""
        hostname = obj.hostname.rstrip(".").lower()
        zones = obj.tenant.domains.filter(
            ownership_status=DomainOwnership.VERIFIED,
        ).values_list("domain", flat=True)
        matches = [
            zone.rstrip(".").lower()
            for zone in zones
            if hostname.endswith("." + zone.rstrip(".").lower())
        ]
        return max(matches, key=len) if matches else None

    def get_cname_host(self, obj) -> str | None:
        zone = self.get_cname_zone(obj)
        return obj.hostname[: -(len(zone) + 1)] if zone else None

    def get_setup_instructions(self, obj) -> str:
        if obj.is_dns_verified:
            return "DNS verified. MateMail can now provision HTTPS for this hostname."
        return (
            f"Create a CNAME for {obj.hostname} pointing to {cname_target()}, "
            "then run Verify."
        )


class CustomHostnameCreateSerializer(serializers.Serializer):
    hostname = serializers.CharField(max_length=512)
    surface = serializers.ChoiceField(choices=CustomHostnameSurface.choices)

    def validate_hostname(self, value):
        try:
            return validate_customer_hostname(value)
        except CustomHostnameValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class CustomHostnameInternalStateSerializer(serializers.Serializer):
    """
    Host-worker callback contract.

    Phase 3 stopped at READY. Phase 4 permits the root-owned worker to report
    ACTIVE only after it has atomically installed the final surface-aware nginx
    vhost. The backend still validates the lifecycle transition.
    """

    provisioning_status = serializers.ChoiceField(
        choices=("provisioning", "ready", "active", "error", "inactive")
    )
    certificate_status = serializers.ChoiceField(
        choices=("not_requested", "issuing", "active", "error", "revoked"),
        required=False,
    )
    last_error = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )

    def validate(self, attrs):
        if attrs["provisioning_status"] in {"ready", "active"}:
            if attrs.get("certificate_status") != "active":
                raise serializers.ValidationError(
                    "READY/ACTIVE requires certificate_status=active."
                )
            attrs["last_error"] = ""
        if attrs["provisioning_status"] == "inactive":
            if attrs.get("certificate_status") != "revoked":
                raise serializers.ValidationError(
                    "INACTIVE requires certificate_status=revoked."
                )
            attrs["last_error"] = ""
        return attrs
