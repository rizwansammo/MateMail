from rest_framework import serializers
from .models import QuarantineMessage


class QuarantineMessageSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = QuarantineMessage
        fields = [
            "id",
            "engine_message_id",
            "sender",
            "recipient",
            "subject",
            "spam_score",
            "status",
            "status_display",
            "received_at",
            "actioned_at",
        ]
