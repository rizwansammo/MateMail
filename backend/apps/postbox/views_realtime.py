"""Authenticated Server-Sent Events for the PostBox web client."""
from django.http import StreamingHttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from .auth import PostBoxSessionAuthentication
from . import realtime


class RealtimeEventView(APIView):
    """
    One content-free mailbox event stream.

    EventSource is same-origin, so the existing host-only HttpOnly PostBox
    session cookie authenticates it. The stream is short-lived by design and
    automatically reconnects, which re-checks revocation/suspension regularly.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [PostBoxSessionAuthentication]

    def get(self, request):
        response = StreamingHttpResponse(
            realtime.stream(request.mailbox.pk),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
