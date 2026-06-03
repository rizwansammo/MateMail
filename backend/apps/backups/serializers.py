from rest_framework import serializers
from .models import BackupJob


class BackupJobSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.SerializerMethodField()

    class Meta:
        model = BackupJob
        fields = [
            "id", "scope", "status", "size_mb", "storage_location",
            "error_message", "started_at", "completed_at", "created_at",
            "restore_metadata", "duration_seconds",
        ]
        read_only_fields = fields

    def get_duration_seconds(self, obj):
        if obj.started_at and obj.completed_at:
            return int((obj.completed_at - obj.started_at).total_seconds())
        return None


class TriggerBackupSerializer(serializers.Serializer):
    scope = serializers.ChoiceField(
        choices=["workspace", "domain", "mailbox"],
        default="workspace",
    )
