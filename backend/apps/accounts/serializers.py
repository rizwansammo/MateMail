from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import User


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "email", "full_name",
            "email_verified", "two_factor_enabled", "is_platform_admin", "created_at",
        ]
        read_only_fields = ["id", "email", "email_verified", "is_platform_admin", "created_at"]


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=10, write_only=True)
    full_name = serializers.CharField(max_length=255)
    workspace_name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    invite_token = serializers.CharField(required=False, allow_blank=False, write_only=True)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate(self, attrs):
        if not attrs.get("invite_token") and not attrs.get("workspace_name", "").strip():
            raise serializers.ValidationError(
                {"workspace_name": "Organization name is required."}
            )
        return attrs


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=10, write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class TwoFactorVerifySerializer(serializers.Serializer):
    partial_token = serializers.CharField()
    code = serializers.CharField(max_length=8, min_length=6)


class TwoFactorSetupConfirmSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)


class TwoFactorDisableSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)


class WorkspaceSwitchSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()


class UpdateProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["full_name"]
