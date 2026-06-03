from django.urls import path
from .views import QueueMessageListView, QueueMessageCancelView

urlpatterns = [
    path("", QueueMessageListView.as_view(), name="queue-list"),
    path("<uuid:pk>/cancel/", QueueMessageCancelView.as_view(), name="queue-cancel"),
]
