from django.urls import path
from .views import BillingView

urlpatterns = [
    path("", BillingView.as_view(), name="billing"),
]
