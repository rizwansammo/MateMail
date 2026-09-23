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
            # Exposed so the Workspace can show mail records and discovery
            # records in separate sections. Without it the UI would have to
            # infer the split from `record_type`, and would start scoring a
            # future record type the moment one was added.
            "is_scored",
        ]
        read_only_fields = fields
