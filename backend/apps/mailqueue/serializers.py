from rest_framework import serializers
from .models import QueueMessage


class QueueMessageSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = QueueMessage
        fields = [
            "id",
            "engine_message_id",
            "sender",
            "recipient",
            "subject",
            "status",
            "status_display",
            "reason",
            "queued_at",
            "last_retry",
            "next_retry",
            "retry_count",
        ]
