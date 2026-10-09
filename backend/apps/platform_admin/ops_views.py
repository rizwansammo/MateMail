"""
System health, backup visibility, and the oversight actions.

The rule this module is written against: report what can be checked, and say
"unknown" for what cannot. A console that prints a green tick for a component
it never contacted is worse than one that prints nothing, because the tick is
what stops somebody looking.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.permissions import IsPlatformAdmin

from .audit import record_platform_action

logger = logging.getLogger(__name__)

#: The three states a component can be in. "unknown" is a first-class answer,
#: not an error — several components genuinely cannot be reached from the
#: application process, and saying so is the honest result.
OK = "ok"
ERROR = "error"
UNKNOWN = "unknown"


class PlatformHealthView(APIView):
    """
    Component health, as far as the application can actually see it.

    WHAT IS CHECKED FOR REAL
        PostgreSQL and Redis are checked by connecting to them, reusing the
        same probes as `/api/health/`. The Mail Engine is checked through its
        adapter, which makes a real call.

    WHAT IS NOT, AND WHY IT SAYS SO
        Postfix, Dovecot, Rspamd, the Celery worker and Celery beat are
        separate processes — several on a different host entirely. The Django
        process has no authoritative way to ask them anything, and inventing a
        green tick from "the database is up" would be exactly the fabrication
        this console must not contain. They are reported as `unknown` with the
        reason and the place an operator can actually look.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from apps.health.views import _check_db, _check_mail_engine, _check_redis

        components = []

        db_status, db_ms = _check_db()
        components.append(_component(
            "PostgreSQL", db_status, db_ms,
            detail="Primary application database.",
        ))

        redis_status, redis_ms = _check_redis()
        components.append(_component(
            "Redis", redis_status, redis_ms,
            detail="Cache, rate-limit counters and Celery broker.",
        ))

        engine_status, engine_ms = _check_mail_engine()
        components.append(_component(
            "Mail Engine API", engine_status, engine_ms,
            detail="The Native Engine's provisioning API.",
        ))

        # Celery is observable in one narrow, honest way: the broker is Redis,
        # and `inspect` reaches the workers through it. A timeout here means
        # "no worker answered", which is a real answer rather than a guess.
        components.append(_celery_component())

        for name in ("Postfix", "Dovecot", "Rspamd"):
            components.append(_component(
                name, UNKNOWN, None,
                detail=(
                    "Runs in the Native Engine, outside this application. "
                    "Authoritative state is in the monitoring stack "
                    "(Prometheus/Grafana) and `docker compose ps` on the "
                    "engine host."
                ),
            ))

        checked = [c for c in components if c["status"] != UNKNOWN]
        overall = OK if all(c["status"] == OK for c in checked) else ERROR

        return Response({
            "overall": overall,
            "checked_at": timezone.now().isoformat(),
            "components": components,
            "note": (
                "Components marked unknown are not reachable from the "
                "application process. They are not assumed healthy."
            ),
        })


def _component(name, status, latency_ms, *, detail="") -> dict:
    return {
        "name": name,
        "status": status,
        "latency_ms": latency_ms if (latency_ms or 0) >= 0 else None,
        "detail": detail,
    }


def _celery_component() -> dict:
    try:
        from config.celery import app as celery_app

        replies = celery_app.control.inspect(timeout=2).ping() or {}
    except Exception as exc:
        # A broker that cannot be reached is an error about Celery, not a
        # crash in the health page.
        logger.warning("Celery inspect failed: %s", exc)
        return _component(
            "Celery workers", UNKNOWN, None,
            detail=f"Could not query the broker: {exc.__class__.__name__}.",
        )

    if not replies:
        return _component(
            "Celery workers", ERROR, None,
            detail="No worker answered a ping within 2 seconds.",
        )
    return _component(
        "Celery workers", OK, None,
        detail=f"{len(replies)} worker(s) responding: {', '.join(sorted(replies))}.",
    )


class PlatformBackupStatusView(APIView):
    """
    Disaster-recovery backup visibility.

    THE HONEST ANSWER, AND WHY IT IS THE RIGHT ONE
        MateMail's real backups are the P6 restic system: a nightly systemd
        timer on the host, with its own restore tools and retention. Its configured repository is local;
        a separate MateServer Azure Blob DR job supplies offsite coverage.
        It is deliberately outside the application — a backup system that the
        application could write to is a backup system that a compromise of the
        application can destroy.

        The consequence is that this process cannot read its state. There is no
        database table, no mounted path and no API. So this endpoint does not
        guess. It reports that platform backup state is not observable from
        here, names where it is observable, and returns the one thing it does
        know authoritatively: the per-organization backup job rows.

        The alternative — deriving "last backup: today" from a timer that is
        merely *configured* — is the fabrication that made the old
        `run_backup_task` report archives it never created.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from apps.backups.models import BackupJob, BackupStatus

        jobs = BackupJob.objects.select_related("tenant").order_by("-created_at")[:20]

        return Response({
            "platform_backups": {
                "status": UNKNOWN,
                "observable_from_application": False,
                "detail": (
                    "MateMail local Restic backups run on the host via "
                    "matemail-backup.timer. Independent whole-server Azure Blob "
                    "disaster-recovery backups run via mateserver-backup.timer. "
                    "Neither backup job is observable by this application, "
                    "so the current success of either is not guessed."
                ),
                "where_to_look": [
                    "systemctl status matemail-backup.timer",
                    "/opt/MateMail/backup/matemail-restore.sh  (local restore validation)",
                    "systemctl status mateserver-backup.timer",
                    "/opt/mateserver-backup/DR-RUNBOOK.md  (Azure disaster recovery)",
                    "Prometheus: matemail_backup_* metrics from the P7 collector",
                    "docs/BACKUP_RESTORE.md",
                ],
            },
            "tenant_backup_jobs": {
                "supported": False,
                "detail": (
                    "Per-organization backup export is not implemented. Jobs "
                    "requested through the MateMail Workspace are recorded "
                    "and explicitly fail; no archive is produced."
                ),
                "recent": [
                    {
                        "id": str(job.id),
                        "tenant": job.tenant.name if job.tenant else None,
                        "scope": job.scope,
                        "status": job.status,
                        "error_message": job.error_message,
                        "created_at": job.created_at.isoformat() if job.created_at else None,
                    }
                    for job in jobs
                ],
                "counts": dict(
                    BackupJob.objects.values_list("status")
                    .annotate(n=Count("id"))
                    .values_list("status", "n")
                ),
            },
        })


# ── Oversight actions ───────────────────────────────────────────────────────

class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, allow_blank=False)


class PlatformAliasDisableView(APIView):
    """
    Disable one alias, for abuse intervention.

    POST disables, DELETE re-enables. The platform operator is not the normal
    alias editor — this exists for the case where an alias is being used to
    relay abuse and the organization is unreachable or unwilling.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        return self._set_status(request, pk, disable=True)

    def delete(self, request, pk):
        return self._set_status(request, pk, disable=False)

    def _set_status(self, request, pk, *, disable: bool):
        from apps.aliases.models import Alias, AliasStatus

        alias = Alias.objects.select_related("tenant").filter(pk=pk).first()
        if alias is None:
            return Response({"detail": "Not found."}, status=404)

        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        alias.status = AliasStatus.DISABLED if disable else AliasStatus.ACTIVE
        alias.save(update_fields=["status", "updated_at"])

        record_platform_action(
            actor=request.user,
            action="alias.disable" if disable else "alias.enable",
            request=request,
            tenant=alias.tenant,
            target_label=alias.source_address,
            reason=serializer.validated_data["reason"],
        )
        return Response({"id": str(alias.id), "status": alias.status})


class PlatformForwardingDisableView(APIView):
    """
    Disable one forwarding rule.

    Forwarding to an external destination is the highest-risk configuration on
    the platform — it is how a compromised mailbox exfiltrates mail and how a
    relay abuse complaint usually starts — so it gets its own intervention.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        return self._set_status(request, pk, disable=True)

    def delete(self, request, pk):
        return self._set_status(request, pk, disable=False)

    def _set_status(self, request, pk, *, disable: bool):
        from apps.forwarding.models import ForwardingRule, ForwardingStatus

        rule = ForwardingRule.objects.select_related(
            "tenant", "source_mailbox").filter(pk=pk).first()
        if rule is None:
            return Response({"detail": "Not found."}, status=404)

        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        rule.status = (
            ForwardingStatus.DISABLED if disable else ForwardingStatus.ACTIVE
        )
        rule.save(update_fields=["status", "updated_at"])

        record_platform_action(
            actor=request.user,
            action="forwarding.disable" if disable else "forwarding.enable",
            request=request,
            tenant=rule.tenant,
            target_label=rule.destination_email,
            reason=serializer.validated_data["reason"],
        )
        return Response({"id": str(rule.id), "status": rule.status})


class PlatformQuarantineActionView(APIView):
    """
    Release or delete one quarantined message, platform-wide.

    Both actions already exist for the organization that owns the message;
    this is the same operation reachable when the operator is handling an
    incident across several organizations at once. Releasing changes the
    message's disposition — it does not return its content, and this endpoint
    has no body to return.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        return self._action(request, pk, release=True)

    def delete(self, request, pk):
        return self._action(request, pk, release=False)

    def _action(self, request, pk, *, release: bool):
        from apps.quarantine.models import QuarantineMessage, QuarantineStatus

        message = QuarantineMessage.objects.select_related("tenant").filter(pk=pk).first()
        if message is None:
            return Response({"detail": "Not found."}, status=404)

        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message.status = (
            QuarantineStatus.RELEASED if release else QuarantineStatus.DELETED
        )
        message.actioned_at = timezone.now()
        message.save(update_fields=["status", "actioned_at"])

        record_platform_action(
            actor=request.user,
            action="quarantine.release" if release else "quarantine.delete",
            request=request,
            tenant=message.tenant,
            target_label=f"{message.sender} -> {message.recipient}",
            reason=serializer.validated_data["reason"],
        )
        return Response({"id": str(message.id), "status": message.status})


class PlatformQueueCancelView(APIView):
    """
    Cancel one queued message.

    Cancel only. Retry and release are not exposed: `QueueMessage` is a record
    of what the engine is doing, and the application has no supported way to
    push a message back into Postfix's queue. A button that appeared to retry
    and did nothing would be the queue equivalent of the fake backup.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        from apps.mailqueue.models import QueueMessage, QueueStatus

        message = QueueMessage.objects.select_related("tenant").filter(pk=pk).first()
        if message is None:
            return Response({"detail": "Not found."}, status=404)

        serializer = ReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not hasattr(QueueStatus, "CANCELLED"):
            return Response(
                {"detail": "Cancellation is not supported by the queue model."},
                status=409,
            )

        message.status = QueueStatus.CANCELLED
        message.save(update_fields=["status"])

        record_platform_action(
            actor=request.user,
            action="queue.cancel",
            request=request,
            tenant=message.tenant,
            target_label=f"{message.sender} -> {message.recipient}",
            reason=serializer.validated_data["reason"],
        )
        return Response({"id": str(message.id), "status": message.status})
