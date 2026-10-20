"""Read-only, fail-closed deployment gate for Hub administrator MFA.

Run inside the DEPLOYED backend before enabling mandatory owner/admin Hub MFA:

    python manage.py audit_hub_admin_mfa_readiness --json

Exit 0 = readiness conditions satisfied, exit 2 = rollout BLOCKED.
No user emails, tokens, code hashes or personal details are ever printed.
"""
import json

from django.core.management.base import BaseCommand
from django.conf import settings

from apps.accounts.models import User, VerifiedRecoveryEmail
from apps.tenants.models import (
    AdminHubMfaMode, MemberRole, MemberStatus, Tenant, TenantMembership,
)


def _independent_recovery(user):
    row = VerifiedRecoveryEmail.objects.filter(
        user_id=user.pk, verified_at__isnull=False
    ).exclude(email="").first()
    return bool(
        row and row.email.strip().casefold() != user.email.strip().casefold()
    )


def evaluate_readiness():
    """No state changes, no PII. Count all active owner and required-admin cases."""
    active_platform_staff = User.objects.filter(
        is_platform_admin=True, is_active=True,
    ).count()
    workspaces = list(
        Tenant.objects.select_related("owner").all().order_by("pk")
    )
    owners = {tenant.owner_id: tenant.owner for tenant in workspaces}
    invalid_owner_memberships = 0
    for tenant in workspaces:
        if not TenantMembership.objects.filter(
            tenant_id=tenant.pk, user_id=tenant.owner_id,
            role=MemberRole.OWNER, status=MemberStatus.ACTIVE,
        ).exists():
            invalid_owner_memberships += 1
    missing_owner_recovery = sum(
        not owner.is_active or not _independent_recovery(owner)
        for owner in owners.values()
    )
    required_admins = list(
        TenantMembership.objects.select_related("user").filter(
            status=MemberStatus.ACTIVE,
            role=MemberRole.ADMIN,
            admin_hub_mfa_policy=AdminHubMfaMode.REQUIRED,
        ).order_by("pk")
    )
    missing_required_admin_recovery = sum(
        not member.user.is_active or not _independent_recovery(member.user)
        for member in required_admins
    )
    problems = []
    if active_platform_staff < 2:
        problems.append("Need at least two active platform security operators.")
    if not owners:
        problems.append("No tenant Owner accounts found; verify deployment database.")
    if invalid_owner_memberships:
        problems.append("Owner/member role integrity check failed.")
    if missing_owner_recovery:
        problems.append("Owner accounts lack verified independent recovery.")
    if missing_required_admin_recovery:
        problems.append("Required administrators lack verified independent recovery.")
    return {
        "ready": not problems,
        "active_platform_operators": active_platform_staff,
        "workspaces_checked": len(workspaces),
        "distinct_owners_checked": len(owners),
        "owners_without_independent_recovery": missing_owner_recovery,
        "invalid_owner_memberships": invalid_owner_memberships,
        "required_admins_checked": len(required_admins),
        "required_admins_without_independent_recovery": missing_required_admin_recovery,
        "owners_with_authenticator": sum(owner.two_factor_enabled for owner in owners.values()),
        "current_release_flags": {
            "policy_enabled": bool(getattr(settings, "HUB_ADMIN_MFA_POLICY_ENABLED", False)),
            "enforcement_ready": bool(getattr(settings, "HUB_ADMIN_MFA_ENFORCEMENT_READY", False)),
            "admin_recovery_enabled": bool(getattr(settings, "HUB_ADMIN_MFA_RECOVERY_ENABLED", False)),
            "owner_emergency_enabled": bool(getattr(settings, "PLATFORM_OWNER_EMERGENCY_RECOVERY_ENABLED", False)),
        },
        "blockers": problems,
    }


class Command(BaseCommand):
    help = "Read-only fail-closed owner/admin MFA release readiness preflight (no personal data)."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        report = evaluate_readiness()
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        else:
            status = "READY" if report["ready"] else "BLOCKED"
            self.stdout.write("MateMail Hub administrator MFA release: " + status)
            for name in ("active_platform_operators", "workspaces_checked",
                         "distinct_owners_checked", "owners_without_independent_recovery",
                         "invalid_owner_memberships", "required_admins_checked",
                         "required_admins_without_independent_recovery"):
                self.stdout.write(f"{name}: {report[name]}")
            for item in report["blockers"]:
                self.stderr.write("BLOCKER: " + item)
        if not report["ready"]:
            # Explicit non-zero shell status, suitable for deployment guard.
            raise SystemExit(2)
