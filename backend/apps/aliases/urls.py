from django.urls import path

from .views import AliasDetailView, AliasListCreateView, AliasStatusView

urlpatterns = [
    path("", AliasListCreateView.as_view(), name="alias-list"),
    path("<uuid:pk>/", AliasDetailView.as_view(), name="alias-detail"),
    path("<uuid:pk>/status/", AliasStatusView.as_view(), name="alias-status"),
]
