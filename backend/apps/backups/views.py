from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from apps.tenants.permissions import IsTenantAdmin
from .models import BackupJob
from .serializers import BackupJobSerializer, TriggerBackupSerializer
from .tasks import run_backup_task


class BackupJobListView(APIView):
    permission_classes = [IsTenantAdmin]

    def get(self, request):
        qs = BackupJob.objects.for_tenant(request.tenant)
        scope = request.query_params.get("scope")
        status_filter = request.query_params.get("status")
        if scope:
            qs = qs.filter(scope=scope)
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(BackupJobSerializer(qs[:100], many=True).data)

    def post(self, request):
        ser = TriggerBackupSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        job = BackupJob.objects.create(
            tenant=request.tenant,
            scope=ser.validated_data["scope"],
        )
        run_backup_task.delay(str(job.id))
        return Response(BackupJobSerializer(job).data, status=status.HTTP_201_CREATED)


class BackupJobDetailView(APIView):
    permission_classes = [IsTenantAdmin]

    def get(self, request, job_id):
        try:
            job = BackupJob.objects.for_tenant(request.tenant).get(id=job_id)
        except BackupJob.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BackupJobSerializer(job).data)
