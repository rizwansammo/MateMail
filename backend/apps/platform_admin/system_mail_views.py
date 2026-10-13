"""Platform Console API for system mail configuration drafts only."""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.permissions import IsPlatformAdmin

from .system_mail import (
    DomainNotReserved, RevisionConflict, SystemMailProposalSerializer,
    proposed_configuration, save_proposed_configuration,
)


class PlatformSystemMailConfigurationView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        return Response(proposed_configuration())

    def post(self, request):
        serializer = SystemMailProposalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            save_proposed_configuration(
                actor=request.user, request=request,
                payload=serializer.validated_data,
            )
        except RevisionConflict:
            return Response(
                {"detail": "Configuration was updated. Refresh before saving."},
                status=status.HTTP_409_CONFLICT,
            )
        except DomainNotReserved:
            return Response(
                {"detail": "Platform domain reservation is missing. No changes saved."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(proposed_configuration(), status=status.HTTP_201_CREATED)


class PlatformSystemMailReadinessView(APIView):
    """Read only; does not create an engine session for customer mailboxes."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from .system_mail_readiness import system_mail_readiness
        return Response(system_mail_readiness())


class PlatformSystemMailHistoryView(APIView):
    """Bounded, secret-free history of immutable platform proposals."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from .models import PlatformSystemMailRevision
        from .system_mail import serialize_revision

        rows = list(PlatformSystemMailRevision.objects.order_by("-revision")[:21])
        return Response({
            "results": [serialize_revision(row) for row in rows[:20]],
            "has_more": len(rows) > 20,
        })


class PlatformSystemMailDnsReadinessView(APIView):
    """Public DNS observations, available to Platform Admins only.

    This deliberately never activates or stages any mail resource.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from .system_mail_dns import inspect_system_mail_dns
        return Response(inspect_system_mail_dns())


class PlatformSystemMailDkimPrepareView(APIView):
    """Generate only target DKIM public DNS material via dedicated engine role."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request):
        from .system_mail_dkim import (
            PrepareSystemDkimSerializer, prepare_system_dkim,
        )
        from .system_mail_stage import StageConflict, StageUnavailable
        serializer = PrepareSystemDkimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            data = prepare_system_dkim(
                actor=request.user, request=request,
                expected_revision=serializer.validated_data["expected_revision"],
            )
        except StageConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except StageUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(data)


class PlatformSystemMailStageView(APIView):
    """Explicit, audited inactive staging; disabled unless separately configured."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request):
        from .system_mail_stage import (
            StageConflict, StageUnavailable, SystemMailStageSerializer,
            stage_system_mail,
        )
        serializer = SystemMailStageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = stage_system_mail(
                actor=request.user, request=request,
                expected_revision=serializer.validated_data["expected_revision"],
            )
        except StageConflict as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except StageUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(result, status=status.HTTP_200_OK)
