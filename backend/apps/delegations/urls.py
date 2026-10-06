from django.urls import path

from .views import DelegationDetailView, DelegationListCreateView

urlpatterns = [
    path("", DelegationListCreateView.as_view(), name="delegation-list"),
    path("<uuid:pk>/", DelegationDetailView.as_view(), name="delegation-detail"),
]
