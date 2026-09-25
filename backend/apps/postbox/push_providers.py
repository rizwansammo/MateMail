"""
Remote push providers for native PostBox: FCM (Android) and WNS (Windows).

ONE BOUNDARY
    Everything provider-specific - endpoints, authentication, request shape and
    what each answer means - lives here. Tasks see one call,
    `provider_for(name).send(device, message)`, and one result, `PushOutcome`.
    Nothing in this module knows about views, sessions or the event table.

WHAT A PUSH CARRIES
    `message` is built by `apps.postbox.push.push_message`: opaque identifiers
    only - never the mailbox address, a sender, subject, body, recipient,
    attachment name, cookie or token. Provider infrastructure is somebody
    else's network, and Microsoft's own guidance is that notifications "should
    never include confidential, sensitive, or personal data".

VERIFIED AGAINST (fetched 2026-09-26)
    FCM  - the HTTP v1 discovery document: `POST v1/projects/{id}/messages:send`
           with scope `https://www.googleapis.com/auth/firebase.messaging`;
           `data` is a string map; `android.priority` is normal|high;
           `Message.token` is "Deprecated: Use `fid` instead. During the
           transition period, this field also accepts a Firebase Installation
           ID". "Manage tokens": UNREGISTERED (404) and INVALID_ARGUMENT (400)
           mean an invalid registration - the latter "only if the payload is
           completely valid", which ours is by construction and by test.
    WNS  - Microsoft Learn, WNS overview, the Windows App SDK push quickstart
           and "request and response headers": Entra ID client credentials at
           `login.microsoftonline.com/{tenant}/oauth2/v2.0/token` with scope
           `https://wns.windows.com/.default`; raw pushes are
           `application/octet-stream` with `X-WNS-Type: wns/raw`; 404 and 410
           mean stop sending to the channel; 406 and 503 carry Retry-After;
           401 means get a new token; the payload limit is 5000 bytes; channel
           URIs must be on notify.windows.com. The legacy Package-SID flow at
           login.live.com is UWP-only and "not compatible with Windows App SDK
           push notifications", so it is not implemented.

NO LIVE CALLS IN TESTS. Every HTTP request goes through an injectable session.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass

import requests
from django.conf import settings
from google.auth.exceptions import GoogleAuthError

logger = logging.getLogger(__name__)

#: What loading and refreshing service-account credentials can raise: a
#: missing or unreadable file, malformed JSON or keys, and google-auth's own
#: refresh and transport failures.
CREDENTIAL_ERRORS = (OSError, ValueError, KeyError, GoogleAuthError)

#: (connect, read) seconds for every provider request.
TIMEOUT = (5, 10)


@dataclass(frozen=True)
class PushOutcome:
    """What one send came to. `code` is a short, safe label for logs and rows."""

    DELIVERED = "delivered"
    #: The provider says this registration will never work again: disable it.
    INVALID_DEVICE = "invalid_device"
    #: Transient (network, throttling, provider 5xx): a bounded retry may help.
    RETRY = "retry"
    #: MateMail is not configured for this provider. Nothing was sent.
    UNAVAILABLE = "unavailable"
    #: Refused for a reason retrying will not fix and that is not the
    #: device's fault (credentials, a request the provider rejects).
    REJECTED = "rejected"

    status: str
    code: str
    retry_after: int | None = None


def _retry_after(response) -> int | None:
    value = (response.headers.get("Retry-After") or "").strip()
    return int(value) if value.isdigit() else None


# ── FCM (Android) ───────────────────────────────────────────────────────────

#: Registration tokens and installation IDs are URL-safe strings.
FCM_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,4096}$")


class FcmPushProvider:
    name = "fcm"
    SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
    ENDPOINT = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
    #: A new-mail wake-up older than this is not worth delivering: the app
    #: refreshes its Inbox whenever it starts anyway.
    TTL = "3600s"

    def __init__(
        self,
        *,
        enabled: bool,
        project_id: str,
        credentials_file: str,
        session=None,
        token_source=None,
    ):
        self.enabled = enabled
        self.project_id = project_id
        self.credentials_file = credentials_file
        self._session = session or requests.Session()
        #: Tests inject `token_source(refresh) -> str`; production uses
        #: google-auth with the service-account file.
        self._token_source = token_source
        self._credentials = None

    def available(self) -> bool:
        return bool(
            self.enabled and self.project_id and (self._token_source or self.credentials_file)
        )

    def _access_token(self, *, refresh: bool) -> str:
        if self._token_source is not None:
            return self._token_source(refresh)
        if self._credentials is None:
            from google.oauth2 import service_account

            self._credentials = service_account.Credentials.from_service_account_file(
                self.credentials_file, scopes=[self.SCOPE]
            )
        if refresh or not self._credentials.valid:
            from google.auth.transport.requests import Request

            self._credentials.refresh(Request(self._session))
        return self._credentials.token

    def send(self, device, message: dict[str, str]) -> PushOutcome:
        if not self.available():
            return PushOutcome(PushOutcome.UNAVAILABLE, "not_configured")
        from .models import PushTokenType

        # `token` accepts both kinds during FCM's transition; `fid` is where
        # installation IDs are going, so they are sent there explicitly.
        target = "fid" if device.token_type == PushTokenType.FID else "token"
        body = {
            "message": {
                target: device.token,
                "data": message,
                "android": {"priority": "high", "ttl": self.TTL},
            }
        }
        url = self.ENDPOINT.format(project=self.project_id)
        for attempt in (1, 2):
            try:
                access_token = self._access_token(refresh=attempt == 2)
            except CREDENTIAL_ERRORS as exc:
                logger.error("PostBox FCM credentials are unusable: %s", type(exc).__name__)
                return PushOutcome(PushOutcome.UNAVAILABLE, "credentials")
            try:
                response = self._session.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=TIMEOUT,
                    allow_redirects=False,
                )
            except requests.RequestException:
                return PushOutcome(PushOutcome.RETRY, "network")
            if response.status_code == 401 and attempt == 1:
                continue  # an expired access token: refresh once
            return self._classify(response)
        return PushOutcome(PushOutcome.REJECTED, "unauthenticated")

    @staticmethod
    def _error_code(response) -> str:
        try:
            details = response.json().get("error", {}).get("details", [])
        except (ValueError, AttributeError):
            return ""
        for detail in details if isinstance(details, list) else []:
            if isinstance(detail, dict) and "errorCode" in detail:
                return str(detail["errorCode"]).lower()
        return ""

    def _classify(self, response) -> PushOutcome:
        status = response.status_code
        if status == 200:
            return PushOutcome(PushOutcome.DELIVERED, "ok")
        code = self._error_code(response)
        if status == 404 or code == "unregistered":
            return PushOutcome(PushOutcome.INVALID_DEVICE, "unregistered")
        if status == 400:
            # INVALID_ARGUMENT. Our payload is fixed and tested, so per FCM's
            # guidance this is the registration, not the message.
            return PushOutcome(PushOutcome.INVALID_DEVICE, code or "invalid_argument")
        if status == 429:
            return PushOutcome(PushOutcome.RETRY, "quota_exceeded", _retry_after(response))
        if status >= 500:
            return PushOutcome(PushOutcome.RETRY, code or "unavailable", _retry_after(response))
        # 401 after a refresh, and 403 (SENDER_ID_MISMATCH, permission). A
        # misconfigured project would answer this for EVERY device, so it must
        # never disable registrations - it is MateMail's problem, not theirs.
        return PushOutcome(PushOutcome.REJECTED, code or f"http_{status}")


# ── WNS (Windows) ───────────────────────────────────────────────────────────

WNS_DOMAIN = "notify.windows.com"


def is_wns_channel(uri: str) -> bool:
    """
    An HTTPS channel URI on notify.windows.com, and nothing else.

    Microsoft: the service "should never push notifications to a channel on
    any other domain" - a spoofed channel would otherwise turn MateMail into a
    sender of authenticated POSTs to wherever the attacker points it. The
    subdomain changes and is not checked. Nothing is fetched to validate it.
    """
    if not isinstance(uri, str) or len(uri) > 2048:
        return False
    try:
        parsed = urllib.parse.urlsplit(uri)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and (host == WNS_DOMAIN or host.endswith("." + WNS_DOMAIN))
    )


class WnsAuthError(Exception):
    """Entra ID refused, or answered without a usable token."""


class WnsPushProvider:
    name = "wns"
    TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    SCOPE = "https://wns.windows.com/.default"
    #: Raw pushes are not cached for an offline device unless asked; one is
    #: enough to wake PostBox when it returns, and an hour-old one is not.
    TTL_SECONDS = 3600
    MAX_PAYLOAD_BYTES = 5000

    def __init__(
        self,
        *,
        enabled: bool,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        session=None,
        clock=time.monotonic,
    ):
        self.enabled = enabled
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._session = session or requests.Session()
        self._clock = clock
        self._access_token = ""
        self._expires_at = 0.0

    def available(self) -> bool:
        return bool(self.enabled and self.tenant_id and self.client_id and self.client_secret)

    def _token(self, *, refresh: bool) -> str:
        if not refresh and self._access_token and self._clock() < self._expires_at:
            return self._access_token
        response = self._session.post(
            self.TOKEN_URL.format(tenant=urllib.parse.quote(self.tenant_id, safe="")),
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": self.SCOPE,
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise WnsAuthError(f"token endpoint answered {response.status_code}")
        try:
            payload = response.json()
            token = str(payload["access_token"])
            # Microsoft's own sample returns expires_in as a string.
            lifetime = int(payload.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError) as exc:
            raise WnsAuthError("unreadable token response") from exc
        self._access_token = token
        self._expires_at = self._clock() + max(lifetime - 300, 60)
        return token

    def send(self, device, message: dict[str, str]) -> PushOutcome:
        if not self.available():
            return PushOutcome(PushOutcome.UNAVAILABLE, "not_configured")
        # Checked at registration too; checked again here because this is the
        # line that actually sends a bearer token to that host.
        if not is_wns_channel(device.token):
            return PushOutcome(PushOutcome.INVALID_DEVICE, "channel_domain")
        body = json.dumps(message, separators=(",", ":")).encode()
        if len(body) > self.MAX_PAYLOAD_BYTES:
            return PushOutcome(PushOutcome.REJECTED, "payload_too_large")
        for attempt in (1, 2):
            try:
                access_token = self._token(refresh=attempt == 2)
            except requests.RequestException:
                return PushOutcome(PushOutcome.RETRY, "network")
            except WnsAuthError:
                logger.error("PostBox WNS authentication failed; check the Entra ID settings")
                return PushOutcome(PushOutcome.UNAVAILABLE, "credentials")
            try:
                response = self._session.post(
                    device.token,
                    data=body,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/octet-stream",
                        "X-WNS-Type": "wns/raw",
                        "X-WNS-Cache-Policy": "cache",
                        "X-WNS-TTL": str(self.TTL_SECONDS),
                    },
                    timeout=TIMEOUT,
                    allow_redirects=False,
                )
            except requests.RequestException:
                return PushOutcome(PushOutcome.RETRY, "network")
            if response.status_code == 401 and attempt == 1:
                continue  # the access token expired: get a new one, once
            return self._classify(response)
        return PushOutcome(PushOutcome.REJECTED, "unauthorized")

    @staticmethod
    def _classify(response) -> PushOutcome:
        status = response.status_code
        if status == 200:
            return PushOutcome(
                PushOutcome.DELIVERED, (response.headers.get("X-WNS-Status") or "ok").lower()[:32]
            )
        if status == 404:
            return PushOutcome(PushOutcome.INVALID_DEVICE, "channel_not_found")
        if status == 410:
            return PushOutcome(PushOutcome.INVALID_DEVICE, "channel_expired")
        if status == 406:
            return PushOutcome(PushOutcome.RETRY, "throttled", _retry_after(response))
        if status >= 500:
            return PushOutcome(PushOutcome.RETRY, f"http_{status}", _retry_after(response))
        # 400, 403, 405, 413 and 401 after a fresh token: MateMail's request or
        # credentials, not the device - so the registration is left alone.
        return PushOutcome(PushOutcome.REJECTED, f"http_{status}")


# ── selection ───────────────────────────────────────────────────────────────

_providers: dict[str, object] = {}


def provider_for(name: str):
    """The configured provider, one per process so access tokens are reused."""
    if name not in _providers:
        if name == "fcm":
            _providers[name] = FcmPushProvider(
                enabled=settings.POSTBOX_FCM_ENABLED,
                project_id=settings.POSTBOX_FCM_PROJECT_ID,
                credentials_file=settings.POSTBOX_FCM_CREDENTIALS_FILE,
            )
        elif name == "wns":
            _providers[name] = WnsPushProvider(
                enabled=settings.POSTBOX_WNS_ENABLED,
                tenant_id=settings.POSTBOX_WNS_TENANT_ID,
                client_id=settings.POSTBOX_WNS_CLIENT_ID,
                client_secret=settings.POSTBOX_WNS_CLIENT_SECRET,
            )
        else:
            raise ValueError(f"unknown push provider {name!r}")
    return _providers[name]


def reset_providers() -> None:
    """Forget the configured providers (tests, and settings that changed)."""
    _providers.clear()
