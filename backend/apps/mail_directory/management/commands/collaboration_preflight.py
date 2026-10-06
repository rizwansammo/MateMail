from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.aliases.models import Alias
from apps.forward_groups.models import ForwardGroup, ForwardGroupStatus
from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.mail_directory.models import (
    AddressClaim,
    AddressKind,
    MailboxAccessGrant,
)
from apps.mailboxes.models import Mailbox, MailboxKind


class Command(BaseCommand):
    help = (
        "Validate collaboration address ownership, access grants and Forward "
        "Group invariants after migrations. Read-only; exits non-zero on drift."
    )

    def handle(self, *args, **options):
        errors: list[str] = []
        expected: dict[str, tuple[str, str, str, str]] = {}

        def expect(*, address, kind, tenant_id, domain_id, source):
            normalized = (address or "").strip().lower()
            previous = expected.get(normalized)
            current = (kind, str(tenant_id), str(domain_id), source)
            if previous is not None:
                errors.append(
                    f"duplicate routable address {normalized}: "
                    f"{previous[3]} and {source}"
                )
                return
            expected[normalized] = current

        for mailbox in Mailbox.objects.all().iterator():
            expect(
                address=mailbox.email,
                kind=(
                    AddressKind.TEAM_BOX
                    if mailbox.kind == MailboxKind.TEAM_BOX
                    else AddressKind.MAILBOX
                ),
                tenant_id=mailbox.tenant_id,
                domain_id=mailbox.domain_id,
                source=f"Mailbox {mailbox.pk}",
            )

        for alias in Alias.objects.all().iterator():
            expect(
                address=alias.source_address,
                kind=AddressKind.ALIAS,
                tenant_id=alias.tenant_id,
                domain_id=alias.domain_id,
                source=f"Alias {alias.pk}",
            )

        for group in ForwardGroup.objects.all().iterator():
            expect(
                address=group.address,
                kind=AddressKind.FORWARD_GROUP,
                tenant_id=group.tenant_id,
                domain_id=group.domain_id,
                source=f"Forward Group {group.pk}",
            )

        claims = {
            claim.address.strip().lower(): claim
            for claim in AddressClaim.objects.all().iterator()
        }

        for address, (kind, tenant_id, domain_id, source) in expected.items():
            claim = claims.get(address)
            if claim is None:
                errors.append(f"missing AddressClaim for {source}: {address}")
                continue
            if claim.kind != kind:
                errors.append(
                    f"AddressClaim kind mismatch for {address}: "
                    f"expected {kind}, got {claim.kind}"
                )
            if str(claim.tenant_id) != tenant_id:
                errors.append(
                    f"AddressClaim tenant mismatch for {address}: "
                    f"expected {tenant_id}, got {claim.tenant_id}"
                )
            if str(claim.domain_id) != domain_id:
                errors.append(
                    f"AddressClaim domain mismatch for {address}: "
                    f"expected {domain_id}, got {claim.domain_id}"
                )

        for address, claim in claims.items():
            if address not in expected:
                errors.append(
                    f"orphan AddressClaim {claim.pk}: {address} ({claim.kind})"
                )

        grant_count = 0
        for grant in MailboxAccessGrant.objects.select_related(
            "tenant",
            "target_mailbox",
            "grantee_mailbox",
        ).iterator():
            grant_count += 1
            try:
                grant.full_clean()
            except ValidationError as exc:
                errors.append(
                    f"invalid mailbox access grant {grant.pk}: "
                    f"{exc.message_dict}"
                )

        group_count = 0
        for group in ForwardGroup.objects.prefetch_related(
            "members__mailbox",
            "allowed_senders__mailbox",
        ).iterator():
            group_count += 1
            members = list(group.members.all())
            if group.status == ForwardGroupStatus.ACTIVE and not members:
                errors.append(
                    f"active Forward Group {group.address} has no members"
                )

            member_ids = set()
            for membership in members:
                mailbox = membership.mailbox
                if mailbox.pk in member_ids:
                    errors.append(
                        f"Forward Group {group.address} contains duplicate member "
                        f"{mailbox.email}"
                    )
                member_ids.add(mailbox.pk)
                if mailbox.tenant_id != group.tenant_id:
                    errors.append(
                        f"Forward Group {group.address} has cross-tenant member "
                        f"{mailbox.email}"
                    )

                if ForwardingRule.objects.filter(
                    source_mailbox=mailbox,
                    destination_email__iexact=group.address,
                    status=ForwardingStatus.ACTIVE,
                ).exists():
                    errors.append(
                        f"delivery loop: {group.address} -> {mailbox.email} -> "
                        f"{group.address}"
                    )

            for sender in group.allowed_senders.all():
                mailbox = sender.mailbox
                if mailbox.tenant_id != group.tenant_id:
                    errors.append(
                        f"Forward Group {group.address} has cross-tenant allowed "
                        f"sender {mailbox.email}"
                    )
                if mailbox.kind != MailboxKind.PERSONAL:
                    errors.append(
                        f"Forward Group {group.address} has non-personal allowed "
                        f"sender {mailbox.email}"
                    )

        if errors:
            preview = "\n".join(f" - {message}" for message in errors[:50])
            if len(errors) > 50:
                preview += f"\n - ... and {len(errors) - 50} more"
            raise CommandError(
                "Collaboration preflight failed with "
                f"{len(errors)} problem(s):\n{preview}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Collaboration preflight passed: "
                f"{len(expected)} address claims, "
                f"{grant_count} access grants, "
                f"{group_count} Forward Groups."
            )
        )
