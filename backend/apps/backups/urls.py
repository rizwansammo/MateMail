from django.urls import path
from .views import BackupJobListView, BackupJobDetailView

urlpatterns = [
    path("", BackupJobListView.as_view()),
    path("<uuid:job_id>/", BackupJobDetailView.as_view()),
]
