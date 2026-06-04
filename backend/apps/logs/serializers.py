from rest_framework import serializers
from .models import MailLog


class MailLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailLog
        fields = [
            "id", "event_type", "source", "result",
            "ip_address", "metadata", "created_at",
        ]
        read_only_fields = fields
