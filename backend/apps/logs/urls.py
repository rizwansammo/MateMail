from django.urls import path
from .views import MailLogListView

urlpatterns = [
    path("", MailLogListView.as_view()),
]
