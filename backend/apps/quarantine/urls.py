from django.urls import path
from .views import QuarantineListView, QuarantineReleaseView, QuarantineDeleteView

urlpatterns = [
    path("", QuarantineListView.as_view(), name="quarantine-list"),
    path("<uuid:pk>/release/", QuarantineReleaseView.as_view(), name="quarantine-release"),
    path("<uuid:pk>/", QuarantineDeleteView.as_view(), name="quarantine-delete"),
]
