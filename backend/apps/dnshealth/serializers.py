from rest_framework import serializers
from .models import DNSRecordCheck


class DNSRecordCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = DNSRecordCheck
        fields = [
            "id",
            "record_type",
            "host",
            "expected_value",
            "detected_value",
            "status",
            "last_checked",
        ]
        read_only_fields = fields
