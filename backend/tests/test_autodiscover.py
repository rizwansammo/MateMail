"""
The Outlook Autodiscover compatibility endpoint.

Two things are being proved here, and the second is the one that would hurt.

    1. The document says what a mail client needs: IMAP 993 implicit TLS,
       submission 587 STARTTLS, authentication required, the full address as
       the username — and says nothing about Exchange, MAPI, EWS, ActiveSync
       or POP, none of which MateMail has.

    2. It is not a mailbox enumeration API. The endpoint is unauthenticated
       and reachable by anyone; if the answer for `alice@customer.example`
       differed in any way from `nobody@customer.example`, a stranger could
       walk a customer's staff directory from a laptop. Several tests below
       exist only to compare those two responses byte for byte.

The XML tests are adversarial on purpose: this parses attacker-controlled XML
from the open internet, which is the classic setting for XXE and SSRF.
"""
import xml.etree.ElementTree as ElementTree

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.autodiscover import eligibility
from apps.autodiscover.views import MAX_REQUEST_BYTES, OUTER_NS, RESPONSE_NS
from apps.domains.models import DomainStatus
from tests.factories import make_domain, make_tenant, make_unverified_domain, make_user

URL = "/autodiscover/autodiscover.xml"

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "autodiscover-tests",
    }
}


def request_xml(address: str) -> str:
    """A request shaped like the one Outlook actually sends."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Autodiscover xmlns="http://schemas.microsoft.com/exchange/autodiscover/'
        'outlook/requestschema/2006">'
        "<Request>"
        f"<EMailAddress>{address}</EMailAddress>"
        "<AcceptableResponseSchema>"
        "http://schemas.microsoft.com/exchange/autodiscover/outlook/responseschema/2006a"
        "</AcceptableResponseSchema>"
        "</Request>"
        "</Autodiscover>"
    )


@override_settings(CACHES=LOCMEM_CACHE)
class AutodiscoverTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@acme.example")
        cls.tenant = make_tenant(owner)
        cls.domain = make_domain(cls.tenant, "customer.example")
        # Eligibility requires the domain to actually be in the mail engine.
        # `make_domain` does not set this, so it is set here deliberately
        # rather than by a factory default that would hide the gate.
        cls.domain.mail_engine_provisioned = True
        cls.domain.save(update_fields=["mail_engine_provisioned"])

    def setUp(self):
        # The endpoint is rate limited per IP and every test here shares one.
        cache.clear()

    def post(self, body, content_type="text/xml", **extra):
        return self.client.post(URL, data=body, content_type=content_type, **extra)

    def settings_for(self, address="alice@customer.example"):
        response = self.post(request_xml(address))
        self.assertEqual(200, response.status_code)
        return response


class AutodiscoverResponseTest(AutodiscoverTestBase):
    """The happy path, and exactly what the document is allowed to say."""

    def test_hosted_domain_receives_settings(self):
        response = self.settings_for()
        self.assertIn("text/xml", response["Content-Type"])

        root = ElementTree.fromstring(response.content)
        self.assertEqual(f"{{{OUTER_NS}}}Autodiscover", root.tag)

        account = root.find(f".//{{{RESPONSE_NS}}}Account")
        self.assertIsNotNone(account, "no Account element in the response")
        self.assertEqual(
            "email", account.findtext(f"{{{RESPONSE_NS}}}AccountType")
        )
        self.assertEqual(
            "settings", account.findtext(f"{{{RESPONSE_NS}}}Action")
        )

    def test_response_is_not_cacheable(self):
        """
        The body embeds the caller's own address. A shared cache that served
        one customer's document to the next would have them configure a client
        with somebody else's login name.
        """
        response = self.settings_for()
        self.assertIn("no-store", response["Cache-Control"])

    def _protocols(self, response):
        root = ElementTree.fromstring(response.content)
        found = {}
        for protocol in root.iter(f"{{{RESPONSE_NS}}}Protocol"):
            kind = protocol.findtext(f"{{{RESPONSE_NS}}}Type")
            found[kind] = {
                child.tag.split("}")[-1]: child.text for child in protocol
            }
        return found

    @override_settings(MAIL_HOSTNAME="mx.example.test")
    def test_imap_block_matches_what_the_engine_serves(self):
        protocols = self._protocols(self.settings_for())
        imap = protocols.get("IMAP")
        self.assertIsNotNone(imap, "no IMAP protocol block")

        # The hostname is configuration, not a literal: a deployment that moved
        # its mail hostname must not keep handing out the old one.
        self.assertEqual("mx.example.test", imap["Server"])
        self.assertEqual("993", imap["Port"])
        self.assertEqual("on", imap["SSL"])
        self.assertEqual("on", imap["AuthRequired"])

    @override_settings(MAIL_HOSTNAME="mx.example.test")
    def test_smtp_block_describes_starttls_and_not_implicit_tls(self):
        """
        The distinction that breaks Outlook if it is wrong.

        587 begins in the clear and upgrades with STARTTLS. POX says that with
        `<Encryption>TLS</Encryption>`; `<SSL>on</SSL>` means implicit TLS from
        the first byte, which is 993's behaviour and not 587's. Advertising
        implicit TLS makes Outlook open a handshake against a listener waiting
        for EHLO, and the user sees a failure that names neither cause.
        """
        protocols = self._protocols(self.settings_for())
        smtp = protocols.get("SMTP")
        self.assertIsNotNone(smtp, "no SMTP protocol block")

        self.assertEqual("mx.example.test", smtp["Server"])
        self.assertEqual("587", smtp["Port"])
        self.assertEqual("off", smtp["SSL"])
        self.assertEqual("TLS", smtp["Encryption"])
        self.assertEqual("on", smtp["AuthRequired"])

    def test_login_name_is_the_full_address(self):
        """
        MateMail authenticates on the whole address. A client told to send the
        local part alone is refused by Dovecot with a password error, and the
        person retypes their password instead of fixing the username.
        """
        protocols = self._protocols(self.settings_for("alice@customer.example"))
        for kind in ("IMAP", "SMTP"):
            self.assertEqual("alice@customer.example", protocols[kind]["LoginName"])

    def test_no_protocol_matemail_does_not_have_is_offered(self):
        """
        POX can describe Exchange, MAPI, EWS, ActiveSync and POP. MateMail has
        none of them, and claiming one makes Outlook fail part-way through
        setup rather than cleanly — after the person has already typed a
        password.
        """
        body = self.settings_for().content.decode()
        for absent in (
            "ExchangeRPC", "ExchangeHTTP", "EXCH", "EXPR", "EXHTTP",
            "MAPI", "EWSUrl", "EwsUrl", "ActiveSync", "MobileSync",
            "OABUrl", "POP3", "<Type>POP",
        ):
            self.assertNotIn(absent, body, f"{absent} must not appear")

    def test_ports_we_do_not_serve_are_absent(self):
        body = self.settings_for().content.decode()
        for port in ("465", "110", "995", "143"):
            self.assertNotIn(f"<Port>{port}</Port>", body)

    def test_every_documented_path_spelling_answers(self):
        """
        Outlook builds this URL itself and does not agree with itself about
        case across versions and platforms. Django's resolver is
        case-sensitive, so each spelling is routed explicitly.
        """
        for path in (
            "/autodiscover/autodiscover.xml",
            "/Autodiscover/Autodiscover.xml",
            "/AutoDiscover/AutoDiscover.xml",
            "/autodiscover/Autodiscover.xml",
            "/Autodiscover/autodiscover.xml",
        ):
            cache.clear()
            response = self.client.post(
                path, data=request_xml("alice@customer.example"),
                content_type="text/xml",
            )
            self.assertEqual(200, response.status_code, path)
            self.assertIn("<Type>IMAP</Type>", response.content.decode(), path)

    def test_a_bare_email_element_is_accepted(self):
        """
        The schema puts EMailAddress in the request namespace; clients in the
        wild send it without one. Refusing those would be refusing a real
        client over a namespace declaration.
        """
        response = self.post(
            "<Autodiscover><Request><EMailAddress>alice@customer.example"
            "</EMailAddress></Request></Autodiscover>"
        )
        self.assertIn("<Type>IMAP</Type>", response.content.decode())


class AutodiscoverEnumerationTest(AutodiscoverTestBase):
    """The endpoint answers about domains. It must never answer about mailboxes."""

    def test_unknown_local_part_gets_the_same_bytes_as_a_real_one(self):
        """
        The core guarantee. No mailbox is created in this test at all, which is
        the point: the endpoint never looks, so there is nothing to look at.
        """
        real = self.post(request_xml("alice@customer.example")).content
        cache.clear()
        invented = self.post(request_xml("nobody-here-at-all@customer.example")).content

        # Identical but for the echoed address, which the caller supplied.
        self.assertEqual(
            real.replace(b"alice@", b"X@"),
            invented.replace(b"nobody-here-at-all@", b"X@"),
        )

    def test_an_existing_mailbox_does_not_change_the_answer(self):
        from tests.factories import make_mailbox

        before = self.post(request_xml("realbox@customer.example")).content
        make_mailbox(self.tenant, self.domain, local_part="realbox")
        cache.clear()
        after = self.post(request_xml("realbox@customer.example")).content

        self.assertEqual(before, after)

    def test_refusals_are_indistinguishable(self):
        """
        An unknown domain, an unverified domain and a malformed address all
        produce the same body and status. Different answers would let a caller
        map which domains MateMail hosts and which are mid-onboarding.
        """
        other_owner = make_user("other@elsewhere.example")
        other_tenant = make_tenant(other_owner, name="Other", slug="other")
        make_unverified_domain(other_tenant, "pending.example")

        bodies = set()
        for address in (
            "alice@not-hosted-anywhere.example",
            "alice@pending.example",
            "not-an-address",
            "@nodomain",
        ):
            cache.clear()
            response = self.post(request_xml(address))
            self.assertEqual(200, response.status_code, address)
            bodies.add(response.content)

        self.assertEqual(1, len(bodies), "refusals must not be distinguishable")

    def test_no_tenant_detail_is_disclosed(self):
        body = self.settings_for().content.decode()
        for secret in (
            str(self.tenant.id), self.tenant.name, self.tenant.slug,
            str(self.domain.id), self.domain.dkim_selector,
        ):
            self.assertNotIn(secret, body, f"{secret!r} leaked into the response")

    def test_a_domain_that_is_not_provisioned_is_not_eligible(self):
        """
        Ownership alone is not enough. A domain verified but never pushed into
        the mail engine has no mailboxes, so settings for it would point a
        client at a server that will refuse every login.
        """
        self.domain.mail_engine_provisioned = False
        self.domain.save(update_fields=["mail_engine_provisioned"])

        self.assertIsNone(eligibility.hosted_domain("customer.example"))

    def test_a_paused_domain_is_not_eligible(self):
        self.domain.status = DomainStatus.PAUSED
        self.domain.save(update_fields=["status"])
        self.assertIsNone(eligibility.hosted_domain("customer.example"))

    def test_a_warning_domain_is_still_eligible(self):
        """
        WARNING means a DNS check is imperfect — often DMARC — not that mail is
        broken. Somebody setting up a client during that window is normal, and
        refusing them would be refusing a working mailbox.
        """
        self.domain.status = DomainStatus.WARNING
        self.domain.save(update_fields=["status"])
        self.assertIsNotNone(eligibility.hosted_domain("customer.example"))


class AutodiscoverHostileInputTest(AutodiscoverTestBase):
    """This parses XML from the open internet."""

    def test_external_entity_does_not_read_a_local_file(self):
        """
        The classic XXE. With a stdlib parser on some configurations this
        substitutes the file's contents into EMailAddress; the response would
        either echo it or behave differently, and either way the file has been
        read.
        """
        attack = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<Autodiscover><Request><EMailAddress>&xxe;</EMailAddress>"
            "</Request></Autodiscover>"
        )
        response = self.post(attack)
        self.assertEqual(200, response.status_code)
        body = response.content.decode()
        self.assertNotIn("root:", body)
        self.assertNotIn("/bin/", body)
        self.assertIn("<Error", body)

    def test_external_entity_does_not_fetch_a_url(self):
        """
        The same hole used outward instead of inward. A parser that resolved
        this would make the endpoint an SSRF primitive against the private
        network the backend sits in — including the Mail Engine's own API.
        """
        attack = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://169.254.169.254/latest/meta-data/">]>'
            "<Autodiscover><Request><EMailAddress>&xxe;</EMailAddress>"
            "</Request></Autodiscover>"
        )
        response = self.post(attack)
        self.assertEqual(200, response.status_code)
        self.assertIn("<Error", response.content.decode())

    def test_entity_expansion_is_refused(self):
        """Billion laughs. A parser that expands this never returns."""
        attack = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz ["
            '<!ENTITY lol "lol">'
            '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
            '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
            "]>"
            "<Autodiscover><Request><EMailAddress>&lol3;</EMailAddress>"
            "</Request></Autodiscover>"
        )
        response = self.post(attack)
        self.assertEqual(200, response.status_code)
        self.assertIn("<Error", response.content.decode())

    def test_malformed_xml_is_refused_without_a_stack_trace(self):
        response = self.post("<Autodiscover><Request>not closed")
        self.assertEqual(200, response.status_code)
        body = response.content.decode()
        self.assertIn("<Error", body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("ParseError", body)

    def test_empty_body_is_refused(self):
        response = self.post("")
        self.assertEqual(200, response.status_code)
        self.assertIn("<Error", response.content.decode())

    def test_oversized_body_is_refused_before_parsing(self):
        """
        Capped on the raw bytes. A parser handed a huge document has already
        lost however safe it is, so the limit must come first.
        """
        padding = "A" * (MAX_REQUEST_BYTES + 1024)
        response = self.post(
            f"<Autodiscover><Request><EMailAddress>alice@customer.example"
            f"</EMailAddress><Padding>{padding}</Padding></Request></Autodiscover>"
        )
        self.assertEqual(413, response.status_code)
        self.assertNotIn("<Type>IMAP</Type>", response.content.decode())

    def test_an_address_cannot_inject_xml(self):
        """
        The login name is the one caller-controlled value in the document. If
        it were interpolated unescaped, a crafted address would let the caller
        write their own protocol blocks into a response another tool might
        trust.
        """
        response = self.post(
            request_xml("a&lt;/LoginName&gt;&lt;Injected/&gt;@customer.example")
        )
        body = response.content.decode()
        self.assertNotIn("<Injected/>", body)

    def test_get_is_not_a_second_way_to_ask(self):
        """
        405, and the Allow header must not contradict it. DRF rebuilds that
        header from the view's declared methods after the response is made, so
        a hand-set value is overwritten — an earlier version of this view
        answered 405 while advertising `Allow: GET`.
        """
        response = self.client.get(URL)
        self.assertEqual(405, response.status_code)
        self.assertIn("POST", response["Allow"])
        self.assertNotIn("GET", response["Allow"])
        self.assertNotIn("<Type>IMAP</Type>", response.content.decode())

    def test_rate_limited_per_ip(self):
        from apps.security.limits import AUTODISCOVER_PER_IP

        for _ in range(AUTODISCOVER_PER_IP.limit):
            self.post(request_xml("alice@customer.example"))

        response = self.post(request_xml("alice@customer.example"))
        self.assertEqual(429, response.status_code)
        self.assertNotIn("<Type>IMAP</Type>", response.content.decode())


class AddressParsingTest(TestCase):
    """`parse_domain` is the only thing standing between XML text and a query."""

    def test_valid_addresses(self):
        self.assertEqual(
            "example.com", eligibility.parse_domain("alice@example.com")
        )
        self.assertEqual(
            "example.com", eligibility.parse_domain("  Alice@Example.COM  ")
        )
        self.assertEqual(
            "sub.example.co.uk", eligibility.parse_domain("a@sub.example.co.uk")
        )

    def test_a_trailing_dot_is_normalised(self):
        self.assertEqual("example.com", eligibility.parse_domain("a@example.com."))

    def test_rejected(self):
        for bad in (
            None, "", "no-at-sign", "@nolocal.com", "alice@", "alice@nodot",
            "alice@-leadinghyphen.com", "two@at@signs.com",
            "alice@exam ple.com", "alice@example.com\nInjected: header",
        ):
            self.assertIsNone(eligibility.parse_domain(bad), repr(bad))

    def test_an_absurdly_long_address_is_rejected_without_matching(self):
        self.assertIsNone(eligibility.parse_domain("a@" + "x" * 5000 + ".com"))
