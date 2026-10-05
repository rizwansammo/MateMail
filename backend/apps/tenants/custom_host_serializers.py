from rest_framework import serializers

from .custom_hosts import (
    CustomHostnameValueError,
    cname_target,
    validate_customer_hostname,
)
from .models import CustomHostname, CustomHostnameSurface


class CustomHostnameSerializer(serializers.ModelSerializer):
    cname_record_type = serializers.SerializerMethodField()
    cname_target = serializers.SerializerMethodField()
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
            "setup_instructions",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_cname_record_type(self, obj) -> str:
        return "CNAME"

    def get_cname_target(self, obj) -> str:
        return cname_target()

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
    Phase 3 worker callback contract.

    ACTIVE is deliberately absent from provisioning_status. The edge worker may
    prepare nginx/TLS and report READY, but Phase 4 owns activation once the
    application routing/authentication code is present.
    """

    provisioning_status = serializers.ChoiceField(
        choices=("provisioning", "ready", "error")
    )
    certificate_status = serializers.ChoiceField(
        choices=("not_requested", "issuing", "active", "error"),
        required=False,
    )
    last_error = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )

    def validate(self, attrs):
        if attrs["provisioning_status"] == "ready":
            if attrs.get("certificate_status") != "active":
                raise serializers.ValidationError(
                    "READY requires certificate_status=active."
                )
            attrs["last_error"] = ""
        return attrs
