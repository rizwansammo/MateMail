from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import AccessToken


class IntegrationPrincipal:
    is_authenticated = True
    is_anonymous = False
    is_active = True

    def __init__(self, integration):
        self.integration = integration
        self.pk = integration.pk
        self.id = integration.pk

    def __str__(self):
        return f"integration:{self.integration.name}"


class IntegrationAccessAuthentication(authentication.BaseAuthentication):
    """Authenticate a revocable mmc_ token bound to one tenant and mailbox."""

    def authenticate(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer mmc_"):
            return None

        raw = auth[len("Bearer "):].strip()
        token = AccessToken.for_raw(raw)
        if token is None or not token.is_active:
            raise exceptions.AuthenticationFailed(
                "Invalid or revoked MateMail connection."
            )

        integration = token.integration
        supplied_tenant = request.META.get("HTTP_X_MATEMAIL_TENANT", "").strip()
        if supplied_tenant != str(integration.tenant_id):
            raise exceptions.AuthenticationFailed(
                "Tenant does not match this connection."
            )
        if not integration.is_active:
            raise exceptions.AuthenticationFailed(
                "This integration has been revoked."
            )
        if not integration.tenant.can_use_mail:
            raise exceptions.AuthenticationFailed(
                "This organization is not active for mail."
            )
        if (
            integration.mailbox.status != "active"
            or not integration.mailbox.mail_engine_provisioned
        ):
            raise exceptions.AuthenticationFailed(
                "The connected mailbox is not active."
            )

        now = timezone.now()
        AccessToken.objects.filter(pk=token.pk).update(last_used_at=now)
        integration.__class__.objects.filter(pk=integration.pk).update(
            last_used_at=now
        )
        request.integration = integration
        request.integration_token = token
        return IntegrationPrincipal(integration), token
