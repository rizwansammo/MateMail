from rest_framework import serializers

from .models import Alias


class AliasSerializer(serializers.ModelSerializer):
    mail_service_ready = serializers.BooleanField(source="mail_engine_provisioned", read_only=True)
    domain_name = serializers.CharField(source="domain.domain", read_only=True)
    destination_email = serializers.EmailField(source="destination_mailbox.email", read_only=True)

    class Meta:
        model = Alias
        fields = [
            "id", "source_address", "domain", "domain_name",
            "destination_mailbox", "destination_email",
            "status", "mail_service_ready", "created_at",
        ]


class AliasCreateSerializer(serializers.Serializer):
    source_local_part = serializers.CharField(max_length=64)
    domain_id = serializers.UUIDField()
    destination_mailbox_id = serializers.UUIDField(required=False)

    def validate(self, data):
        # Make the retired API shape fail loudly. Silently ignoring a legacy
        # external destination would make an old client think it created
        # forwarding when it did not.
        if "destination_address" in self.initial_data:
            raise serializers.ValidationError({
                "destination_address": (
                    "Aliases can only point to a MateMail mailbox. "
                    "Use Forwarding for an external destination."
                )
            })
        if not data.get("destination_mailbox_id"):
            raise serializers.ValidationError({
                "destination_mailbox_id": "This field is required."
            })
        return data
