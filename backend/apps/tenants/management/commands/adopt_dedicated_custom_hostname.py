from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tenants.custom_hosts import (
    check_custom_hostname_dns,
    invalidate_custom_hostname_cache,
    normalize_hostname,
)
from apps.tenants.models import (
    CUSTOM_HOSTNAME_LIVE_STATES,
    CustomHostname,
    CustomHostnameCertificateStatus,
    CustomHostnameDNSStatus,
    CustomHostnameProvisioningStatus,
    CustomHostnameSurface,
    Tenant,
)


class Command(BaseCommand):
    help = (
        "Adopt one existing DEDICATED_TENANT_HOSTS hostname into the normal "
        "custom-host lifecycle without issuing a new certificate. Intended "
        "only for the controlled NetaMate Phase 5 production pilot."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-slug", required=True)
        parser.add_argument("--hostname", required=True)
        parser.add_argument(
            "--surface",
            required=True,
            choices=[CustomHostnameSurface.HUB, CustomHostnameSurface.POSTBOX],
        )

    def handle(self, *args, **options):
        tenant_slug = options["tenant_slug"].strip()
        hostname = normalize_hostname(options["hostname"])
        surface = options["surface"]

        dedicated = getattr(settings, "DEDICATED_TENANT_HOSTS", {})
        if dedicated.get(hostname) != tenant_slug:
            raise CommandError(
                "Refusing adoption: the hostname is not currently dedicated "
                "to that tenant in DEDICATED_TENANT_HOSTS."
            )

        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant is None:
            raise CommandError("Tenant does not exist.")
        if not tenant.can_use_mail:
            raise CommandError(
                "Tenant is not currently eligible for custom-host activation."
            )

        verified, customer_message, technical = check_custom_hostname_dns(hostname)
        if not verified:
            suffix = f" ({technical})" if technical else ""
            raise CommandError(
                "Direct CNAME verification failed: "
                f"{customer_message}{suffix}"
            )

        now = timezone.now()

        try:
            with transaction.atomic():
                Tenant.objects.select_for_update().get(pk=tenant.pk)

                conflict = (
                    CustomHostname.objects.filter(
                        hostname=hostname,
                        provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES,
                    )
                    .exclude(tenant=tenant, surface=surface)
                    .exists()
                )
                if conflict:
                    raise CommandError(
                        "That hostname already belongs to another live custom-host mapping."
                    )

                surface_conflict = (
                    CustomHostname.objects.filter(
                        tenant=tenant,
                        surface=surface,
                        provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES,
                    )
                    .exclude(hostname=hostname)
                    .exists()
                )
                if surface_conflict:
                    raise CommandError(
                        "That tenant already has another live hostname for this surface."
                    )

                row = (
                    CustomHostname.objects.select_for_update()
                    .filter(
                        tenant=tenant,
                        hostname=hostname,
                        surface=surface,
                        provisioning_status__in=CUSTOM_HOSTNAME_LIVE_STATES,
                    )
                    .first()
                )

                if row is None:
                    row = CustomHostname.objects.create(
                        tenant=tenant,
                        hostname=hostname,
                        surface=surface,
                        dns_status=CustomHostnameDNSStatus.VERIFIED,
                        dns_verified_at=now,
                        dns_last_checked_at=now,
                        provisioning_status=CustomHostnameProvisioningStatus.READY,
                        certificate_status=CustomHostnameCertificateStatus.ACTIVE,
                    )
                    action = "created"
                else:
                    if row.provisioning_status == CustomHostnameProvisioningStatus.ACTIVE:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"{hostname} is already ACTIVE for {tenant_slug}/{surface}."
                            )
                        )
                        return

                    row.dns_status = CustomHostnameDNSStatus.VERIFIED
                    row.dns_verified_at = now
                    row.dns_last_checked_at = now
                    row.provisioning_status = CustomHostnameProvisioningStatus.READY
                    row.certificate_status = CustomHostnameCertificateStatus.ACTIVE
                    row.last_error = ""
                    row.save(
                        update_fields=[
                            "dns_status",
                            "dns_verified_at",
                            "dns_last_checked_at",
                            "provisioning_status",
                            "certificate_status",
                            "last_error",
                            "updated_at",
                        ]
                    )
                    action = "updated"
        except IntegrityError as exc:
            raise CommandError(
                "Database uniqueness refused this adoption; inspect existing mappings."
            ) from exc

        invalidate_custom_hostname_cache(hostname)
        self.stdout.write(
            self.style.SUCCESS(
                f"{action}: {hostname} -> {tenant_slug}/{surface} is READY. "
                "The root-owned worker must verify the existing host certificate "
                "and install the final generated vhost before it becomes ACTIVE."
            )
        )
