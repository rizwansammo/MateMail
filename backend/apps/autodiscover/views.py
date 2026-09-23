"""
The Outlook Autodiscover (POX) compatibility endpoint.

WHAT THIS IS
    A discovery service. It tells a mail client where MateMail's IMAP and
    submission servers are, on the one URL Outlook looks for. It carries no
    mail, authenticates nobody, and touches no mailbox.

WHAT THIS IS NOT
    Exchange. The response describes an IMAP/SMTP account and nothing else — no
    Exchange protocol block, no MAPI, no EWS, no ActiveSync, no OAB, no
    free/busy. Outlook's Autodiscover schema can express all of those; emitting
    any of them would claim capabilities MateMail does not have, and Outlook
    would then fail part-way through account setup rather than cleanly.

THE THREAT MODEL
    Unauthenticated, reachable by anyone, and it accepts XML — which is the
    combination that produces XXE, SSRF and file disclosure. Every mitigation
    is in `_parse_request` and its docstring.

    The second risk is quieter: a discovery endpoint that answers differently
    for a real address than an invented one is a mailbox enumeration API. See
    `eligibility.py`.
"""
from __future__ import annotations

import logging
import xml.sax.saxutils as saxutils

from defusedxml.ElementTree import fromstring as safe_fromstring
from defusedxml.common import DefusedXmlException
from django.conf import settings
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView

from apps.security import ratelimit
from apps.security.client_ip import get_client_ip
from apps.security.limits import AUTODISCOVER_PER_IP

from . import eligibility

logger = logging.getLogger(__name__)

#: Outlook's request is a few hundred bytes. Anything beyond this is not a mail
#: client, and the cap is applied to the raw body BEFORE parsing — a parser
#: given a multi-gigabyte document has already lost, however safe it is.
MAX_REQUEST_BYTES = 8 * 1024

#: The POX response namespaces, fixed by Microsoft. `RESPONSE_NS` is the
#: protocol version this endpoint speaks; a client that wants a different one
#: says so in its own `AcceptableResponseSchema`, which is why the request's
#: value is checked rather than assumed.
REQUEST_NS = "http://schemas.microsoft.com/exchange/autodiscover/outlook/requestschema/2006"
RESPONSE_NS = "http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a"
OUTER_NS = "http://schemas.microsoft.com/exchange/autodiscover/responseschema/2006"


def _xml(body: str, status: int = 200) -> HttpResponse:
    """
    An XML response, never cached.

    `no-store` because the body embeds the caller's own address. A shared proxy
    that cached one customer's response and served it to the next would hand
    out somebody else's login name, and the client would configure an account
    with it.
    """
    response = HttpResponse(body, content_type="text/xml; charset=utf-8", status=status)
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _error_response(message: str, status: int = 200) -> HttpResponse:
    """
    The POX "no settings here" answer.

    HTTP 200 with an Error element by default, because that is what the schema
    says and what clients parse. A 404 makes Outlook retry the whole discovery
    sequence against other URLs, which is slower for the user and noisier for
    us, and tells an attacker exactly as much.

    `message` is one of this module's own constants. It is never built from
    request data, so nothing the caller sends can be reflected.
    """
    return _xml(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Autodiscover xmlns="{OUTER_NS}">\n'
        f"  <Response>\n"
        f'    <Error Time="00:00:00.0000000" Id="0">\n'
        f"      <ErrorCode>600</ErrorCode>\n"
        f"      <Message>{saxutils.escape(message)}</Message>\n"
        f"      <DebugData />\n"
        f"    </Error>\n"
        f"  </Response>\n"
        f"</Autodiscover>\n",
        status=status,
    )


#: One message for every refusal. An unknown domain, an unverified domain, a
#: malformed address and unparseable XML all produce this exact string, so the
#: response body cannot be used to tell them apart.
GENERIC_REFUSAL = "The requested configuration is not available."


def _settings_response(client: eligibility.ClientSettings) -> HttpResponse:
    """
    The POX account settings document.

    On `<SSL>` and `<Encryption>`: IMAP on 993 is implicit TLS, so `SSL` is
    `on`. Submission on 587 is STARTTLS — the session opens in the clear and
    upgrades — and `<Encryption>TLS</Encryption>` is how POX says that, as
    distinct from `SSL` which means implicit. Setting `<SSL>on</SSL>` for the
    SMTP protocol block would make Outlook open a TLS handshake against a
    listener expecting EHLO, and the user would see a connection failure that
    names neither port nor protocol.

    Every value is escaped even though the only caller-controlled one is the
    login name, which has already been through an address regex. Escaping the
    lot means a future field added here cannot become an injection by being
    forgotten.
    """
    login = saxutils.escape(client.login_name)
    imap_host = saxutils.escape(client.imap_server)
    smtp_host = saxutils.escape(client.smtp_server)

    return _xml(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<Autodiscover xmlns="{OUTER_NS}">\n'
        f'  <Response xmlns="{RESPONSE_NS}">\n'
        f"    <Account>\n"
        f"      <AccountType>email</AccountType>\n"
        f"      <Action>settings</Action>\n"
        f"      <Protocol>\n"
        f"        <Type>IMAP</Type>\n"
        f"        <Server>{imap_host}</Server>\n"
        f"        <Port>{client.imap_port}</Port>\n"
        f"        <LoginName>{login}</LoginName>\n"
        f"        <SSL>on</SSL>\n"
        f"        <SPA>off</SPA>\n"
        f"        <AuthRequired>on</AuthRequired>\n"
        f"      </Protocol>\n"
        f"      <Protocol>\n"
        f"        <Type>SMTP</Type>\n"
        f"        <Server>{smtp_host}</Server>\n"
        f"        <Port>{client.smtp_port}</Port>\n"
        f"        <LoginName>{login}</LoginName>\n"
        f"        <SSL>off</SSL>\n"
        f"        <Encryption>{saxutils.escape(client.smtp_encryption)}</Encryption>\n"
        f"        <SPA>off</SPA>\n"
        f"        <AuthRequired>on</AuthRequired>\n"
        f"        <UsePOPAuth>off</UsePOPAuth>\n"
        f"        <SMTPLast>off</SMTPLast>\n"
        f"      </Protocol>\n"
        f"    </Account>\n"
        f"  </Response>\n"
        f"</Autodiscover>\n"
    )


def _parse_request(body: bytes) -> str | None:
    """
    The requested address, or None if this is not a request we can answer.

    SAFETY, in the order the risks arrive:

      - Size is capped by the caller before we are reached, so a huge body is
        refused without being parsed.
      - `defusedxml` replaces the parser. It refuses DTDs, entity declarations
        and external entity resolution, which removes XXE (reading
        /etc/passwd), billion-laughs expansion, and the external-entity fetch
        that would make this endpoint an SSRF primitive against the private
        network it sits in.
      - Nothing from the document is ever used to build a URL, a path, a
        hostname or a query. The only value taken out is text, and it goes
        straight into an address regex.
      - Every parse failure returns None and is logged without the body. A
        stack trace would disclose the parser and the file layout; the body may
        contain whatever the sender chose to put in it.

    The element is matched in both the documented namespace and without one:
    the schema says `EMailAddress` in the request namespace, and clients in the
    wild send it bare. Accepting both costs nothing and avoids refusing a
    client over a namespace declaration.
    """
    if not body:
        return None

    try:
        root = safe_fromstring(body)
    except DefusedXmlException:
        # Deliberately its own branch: this is a rejected DTD or entity, i.e.
        # somebody probing, not a client with a typo. Worth seeing separately
        # in the log, and still answered identically.
        logger.warning("Autodiscover: refused an XML document with entities or a DTD")
        return None
    except Exception:
        logger.info("Autodiscover: unparseable request body")
        return None

    for path in (f".//{{{REQUEST_NS}}}EMailAddress", ".//EMailAddress"):
        element = root.find(path)
        if element is not None and element.text:
            return element.text.strip()
    return None


@method_decorator(csrf_exempt, name="dispatch")
class AutodiscoverView(APIView):
    """
    POST /autodiscover/autodiscover.xml

    Mounted at the root rather than under `/api/`, because Outlook constructs
    this URL itself and will not look anywhere else.

    Unauthenticated by necessity: a client asking where the server is has not
    got an account yet. `authentication_classes` is emptied rather than left to
    the project default so that a future change to the default cannot quietly
    start requiring a session here — and so a stray cookie or Bearer header on
    the request is never interpreted as a login.
    """

    authentication_classes: list = []
    permission_classes: list = []

    #: POST only. Declared here rather than by omitting the handlers,
    #: because DRF rebuilds the `Allow` header from this list after the
    #: response is built — a hand-set `Allow` is silently overwritten, and
    #: the 405 then advertises methods it refuses. Some clients and most
    #: monitoring probe with GET; they get a clean 405 naming POST.
    http_method_names = ["post", "options"]

    def post(self, request, *_args, **_kwargs):
        client_ip = get_client_ip(request)
        decision = ratelimit.hit(
            AUTODISCOVER_PER_IP.bucket,
            client_ip or "unknown",
            limit=AUTODISCOVER_PER_IP.limit,
            window=AUTODISCOVER_PER_IP.window,
        )
        if not decision.allowed:
            # 429 rather than the POX error: this is about the caller, not the
            # domain, and a client that backs off and retries should succeed.
            return _xml(
                '<?xml version="1.0" encoding="utf-8"?>\n'
                f'<Autodiscover xmlns="{OUTER_NS}"><Response /></Autodiscover>\n',
                status=429,
            )

        body = request.body or b""
        if len(body) > MAX_REQUEST_BYTES:
            logger.warning(
                "Autodiscover: request body of %d bytes refused (limit %d)",
                len(body), MAX_REQUEST_BYTES,
            )
            return _error_response(GENERIC_REFUSAL, status=413)

        address = _parse_request(body)
        domain_name = eligibility.parse_domain(address)
        if not domain_name or not eligibility.hosted_domain(domain_name):
            # One branch, one message. A malformed address, an unknown domain
            # and a domain we host but have not verified are indistinguishable
            # from outside — which is the point.
            return _error_response(GENERIC_REFUSAL)

        return _settings_response(eligibility.settings_for(address))



def autodiscover_settings_summary() -> dict:
    """
    The discovery record a customer publishes, for the Workspace UI.

    Here rather than in the frontend so the hostname has one source. A literal
    in a React component would keep pointing at the old host after a
    deployment moved it, and nothing would fail — customers would simply be
    told to publish a record that goes nowhere.
    """
    return {
        "record_type": "SRV",
        "host": "_autodiscover._tcp",
        "priority": 0,
        "weight": 0,
        "port": 443,
        "target": f"{settings.AUTODISCOVER_HOST}.",
    }
