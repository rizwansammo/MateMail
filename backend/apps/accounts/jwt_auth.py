"""Reject access tokens issued before a successful Hub credential recovery.

All existing tokens without a version claim are version zero, so this is fully
backward compatible until an account's version is increased by a reset.
"""
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class HubSecurityJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if validated_token.get("hub_security_epoch", 0) != user.hub_security_epoch:
            raise AuthenticationFailed("Session revoked by account security recovery.")
        if user.hub_mfa_reenrollment_required:
            raise AuthenticationFailed("New authenticator enrollment is required.")
        return user
