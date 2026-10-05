from django.http import JsonResponse, HttpResponseNotFound

from .dedicated import custom_hostname_surface


class CustomHostnameSurfaceGuardMiddleware:
    """
    Keep a customer custom hostname on exactly the surface it was assigned.

    nginx enforces the same split for defense in depth, but the application is
    authoritative too: a future proxy edit must not turn a PostBox hostname
    into a Hub/Platform entry point or expose PostBox through a Hub hostname.

    Canonical MateMail hosts and the legacy deployment-only NetaMate bindings
    are unchanged; this guard applies only to ACTIVE database-backed custom
    hostnames.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        surface = custom_hostname_surface(request)
        if surface is None:
            return self.get_response(request)

        path = request.path_info

        # Operator/internal surfaces never exist on customer hostnames.
        if (
            path.startswith("/api/internal/")
            or path.startswith("/api/platform/")
            or path.startswith("/django-admin/")
        ):
            return self._not_found(path)

        if surface == "hub":
            if path.startswith("/api/postbox/"):
                return self._not_found(path)
            return self.get_response(request)

        if surface == "postbox":
            # PostBox is a mailbox identity surface. Workspace JWT/auth/team/
            # domain APIs are intentionally absent even when the mailbox belongs
            # to the same organization.
            if path.startswith("/api/") and not (
                path.startswith("/api/postbox/")
                or path.startswith("/api/health/")
            ):
                return self._not_found(path)
            return self.get_response(request)

        # Database choices should make this impossible. Fail closed if a future
        # surface is added without defining its route policy here.
        return self._not_found(path)

    @staticmethod
    def _not_found(path):
        if path.startswith("/api/"):
            return JsonResponse({"detail": "Not found."}, status=404)
        return HttpResponseNotFound("Not found.")
