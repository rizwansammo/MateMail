"""
Remote images in a mailbox's own HTML signature, fetched by MateMail for the
native PostBox preview (apps/postbox/signature_images.py).

What must never regress: only images already in the mailbox's STORED
signature can be fetched (never a URL from the client, never another
mailbox's signature, never a message); the fetch reaches only public https
hosts on 443 - checked again on every redirect and pinned to the checked
address - and returns only bounded, genuinely-image bytes.

No test touches the network: name resolution and the connection are fakes.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import signature_images as si
from apps.postbox.models import MailSignature, PostBoxSession, SignatureKind
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user

LOGIN = "/api/postbox/auth/login/"

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082"
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-signature-image-tests",
    }
}

SIGNATURE_HTML = (
    '<table style="width:600px"><tr><td>'
    '<img src="https://netamate.com/static/img/logo.png" width="72" alt="NetaMate">'
    '<img src="cid:inline@x" alt="inline">'
    '<img src="http://plain.example/x.png" alt="plain">'
    '</td><td><b>Rizwan Sammo</b>'
    '<img src="HTTPS://cdn.example.com/badge.gif?v=1&amp;s=2" alt="badge">'
    "</td></tr></table>"
)


class FakeUpstream:
    def __init__(self, status=200, headers=None, chunks=(PNG,)):
        self.status = status
        self.headers = headers if headers is not None else {"Content-Type": "image/png"}
        self.chunks = list(chunks)
        self.released = False

    def stream(self, _size):
        yield from self.chunks

    def release_conn(self):
        self.released = True


def resolver(table):
    """getaddrinfo by hostname, from [table]."""

    def resolve(host, _port):
        if host not in table:
            raise si.SignatureImageError("dns")
        return list(table[host])

    return resolve


class Opener:
    """Answers by hostname, in order; records (address, host, path)."""

    def __init__(self, answers):
        self.answers = {host: list(responses) for host, responses in answers.items()}
        self.calls = []

    def __call__(self, address, host, path):
        self.calls.append((address, host, path))
        return self.answers[host].pop(0)


def fetch(url, table, answers):
    opener = Opener(answers)
    return si.fetch(url, resolve=resolver(table), open_connection=opener), opener


class SourcesTest(SimpleTestCase):
    def test_only_https_images_in_document_order(self):
        self.assertEqual(
            [
                "https://netamate.com/static/img/logo.png",
                "HTTPS://cdn.example.com/badge.gif?v=1&s=2",
            ],
            si.https_sources(SIGNATURE_HTML),
        )

    def test_nothing_from_empty_html(self):
        self.assertEqual([], si.https_sources(""))


class AddressTest(SimpleTestCase):
    def test_every_non_public_address_is_refused(self):
        for address in (
            "127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1",
            "169.254.169.254",  # cloud metadata
            "100.64.0.1",  # carrier-grade NAT
            "0.0.0.0", "224.0.0.1", "240.0.0.1",
            "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
        ):
            with self.subTest(address=address):
                with self.assertRaises(si.SignatureImageError) as caught:
                    si.public_addresses("h", resolve=lambda *_: [address])
                self.assertEqual("blocked_address", caught.exception.code)

    def test_a_mixed_answer_is_refused_whole(self):
        with self.assertRaises(si.SignatureImageError):
            si.public_addresses("h", resolve=lambda *_: ["93.184.216.34", "10.0.0.1"])

    def test_public_addresses_pass(self):
        self.assertEqual(
            ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
            si.public_addresses(
                "h",
                resolve=lambda *_: ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
            ),
        )


class FetchTest(SimpleTestCase):
    PUBLIC = {"netamate.com": ["93.184.216.34"]}

    def test_a_png_is_fetched_from_the_checked_address(self):
        image, opener = fetch(
            "https://netamate.com/static/img/logo.png",
            self.PUBLIC,
            {"netamate.com": [FakeUpstream()]},
        )
        self.assertEqual(("image/png", PNG), (image.content_type, image.data))
        self.assertEqual(
            [("93.184.216.34", "netamate.com", "/static/img/logo.png")], opener.calls,
            "pinned to the address that was checked, TLS for the hostname",
        )

    def test_the_sniffed_type_is_served_not_the_declared_one(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 40
        image, _ = fetch(
            "https://netamate.com/a",
            self.PUBLIC,
            {"netamate.com": [FakeUpstream(headers={"Content-Type": "image/png"}, chunks=[jpeg])]},
        )
        self.assertEqual("image/jpeg", image.content_type)

    def test_refusals(self):
        cases = {
            "http://netamate.com/a.png": "scheme",
            "ftp://netamate.com/a.png": "scheme",
            "https://netamate.com:8443/a.png": "port",
            "https://user:pw@netamate.com/a.png": "userinfo",
            "https:///a.png": "host",
        }
        for url, code in cases.items():
            with self.subTest(url=url):
                with self.assertRaises(si.SignatureImageError) as caught:
                    fetch(url, self.PUBLIC, {})
                self.assertEqual(code, caught.exception.code)

    def test_a_private_host_is_never_connected_to(self):
        opener = Opener({})
        with self.assertRaises(si.SignatureImageError) as caught:
            si.fetch(
                "https://intranet.example/logo.png",
                resolve=lambda *_: ["10.0.0.5"],
                open_connection=opener,
            )
        self.assertEqual("blocked_address", caught.exception.code)
        self.assertEqual([], opener.calls)

    def test_redirects_are_checked_hop_by_hop(self):
        def redirect(to):
            return FakeUpstream(status=302, headers={"Location": to})

        for target, extra, code in (
            ("http://netamate.com/plain.png", {}, "scheme"),
            ("https://internal.example/x.png", {"internal.example": ["192.168.0.10"]}, "blocked_address"),
            ("https://netamate.com:444/x.png", {}, "port"),
        ):
            with self.subTest(target=target):
                with self.assertRaises(si.SignatureImageError) as caught:
                    fetch(
                        "https://netamate.com/logo.png",
                        {**self.PUBLIC, **extra},
                        {"netamate.com": [redirect(target)]},
                    )
                self.assertEqual(code, caught.exception.code)

        # A relative redirect to a public image is followed.
        image, opener = fetch(
            "https://netamate.com/logo.png",
            self.PUBLIC,
            {"netamate.com": [redirect("/static/real.png"), FakeUpstream()]},
        )
        self.assertEqual(PNG, image.data)
        self.assertEqual("/static/real.png", opener.calls[-1][2])

    def test_redirects_are_bounded(self):
        loop = [FakeUpstream(status=302, headers={"Location": "/again"}) for _ in range(10)]
        with self.assertRaises(si.SignatureImageError) as caught:
            fetch("https://netamate.com/a", self.PUBLIC, {"netamate.com": loop})
        self.assertEqual("redirects", caught.exception.code)

    def test_size_is_bounded_by_header_and_by_body(self):
        declared = FakeUpstream(
            headers={"Content-Type": "image/png", "Content-Length": str(si.MAX_BYTES + 1)}
        )
        streamed = FakeUpstream(chunks=[PNG, b"\0" * si.MAX_BYTES])
        for upstream in (declared, streamed):
            with self.subTest(declared=upstream is declared):
                with self.assertRaises(si.SignatureImageError) as caught:
                    fetch("https://netamate.com/a", self.PUBLIC, {"netamate.com": [upstream]})
                self.assertEqual("too_large", caught.exception.code)
                self.assertTrue(upstream.released)

    def test_only_real_raster_images(self):
        for headers, chunks, code in (
            ({"Content-Type": "text/html"}, [b"<html>"], "content_type"),
            ({"Content-Type": "image/svg+xml"}, [b"<svg onload=x>"], "content_type"),
            ({"Content-Type": "image/png"}, [b"<html>not a png</html>"], "not_image"),
            ({}, [PNG], "content_type"),
        ):
            with self.subTest(headers=headers):
                with self.assertRaises(si.SignatureImageError) as caught:
                    fetch(
                        "https://netamate.com/a",
                        self.PUBLIC,
                        {"netamate.com": [FakeUpstream(headers=headers, chunks=chunks)]},
                    )
                self.assertEqual(code, caught.exception.code)

    def test_upstream_errors_and_the_deadline(self):
        with self.assertRaises(si.SignatureImageError) as caught:
            fetch("https://netamate.com/a", self.PUBLIC,
                  {"netamate.com": [FakeUpstream(status=404)]})
        self.assertEqual("status", caught.exception.code)

        ticks = iter([0.0, 0.0, si.DEADLINE_SECONDS + 1, si.DEADLINE_SECONDS + 2])
        with self.assertRaises(si.SignatureImageError) as caught:
            si.fetch(
                "https://netamate.com/a",
                resolve=resolver(self.PUBLIC),
                open_connection=Opener({"netamate.com": [FakeUpstream(chunks=[PNG[:8], PNG[8:]])]}),
                clock=lambda: next(ticks),
            )
        self.assertEqual("timeout", caught.exception.code)


def make_mailbox(tenant, domain, local_part):
    return Mailbox.objects.create(
        tenant=tenant, domain=domain, local_part=local_part,
        email=f"{local_part}@{domain.domain}", full_name=local_part.title(),
        status=MailboxStatus.ACTIVE, mail_engine_provisioned=True,
    )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class EndpointTest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)
        owner = make_user("owner@acme.test")
        tenant = make_tenant(owner, name="Acme", slug="acme")
        domain = Domain.objects.create(
            tenant=tenant, domain="acme.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = make_mailbox(tenant, domain, "alice")
        self.bob = make_mailbox(tenant, domain, "bob")
        self.signature = MailSignature.objects.create(
            mailbox=self.alice, name="Corporate", kind=SignatureKind.HTML,
            html=SIGNATURE_HTML,
        )

    def login_as(self, mailbox):
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            client = APIClient()
            response = client.post(
                LOGIN, {"email": mailbox.email, "password": "synthetic"}, format="json"
            )
            assert response.status_code == 200, response.data
        assert PostBoxSession.objects.filter(mailbox=mailbox).exists()
        return client

    def url(self, signature=None, index=0):
        return f"/api/postbox/signatures/{(signature or self.signature).pk}/html-images/{index}/"

    def test_the_owner_gets_the_image_fetched_from_their_own_signature(self):
        client = self.login_as(self.alice)
        with mock.patch.object(si, "fetch", return_value=si.FetchedImage("image/png", PNG)) as f:
            response = client.get(self.url(index=1))
        self.assertEqual(200, response.status_code)
        self.assertEqual(PNG, response.content)
        self.assertEqual("image/png", response["Content-Type"])
        self.assertEqual("nosniff", response["X-Content-Type-Options"])
        self.assertIn("sandbox", response["Content-Security-Policy"])
        f.assert_called_once_with("HTTPS://cdn.example.com/badge.gif?v=1&s=2")

    def test_it_is_reused_for_the_same_signature_version(self):
        client = self.login_as(self.alice)
        with mock.patch.object(si, "fetch", return_value=si.FetchedImage("image/png", PNG)) as f:
            client.get(self.url())
            client.get(self.url())
        self.assertEqual(1, f.call_count)

    def test_another_mailbox_cannot_use_it(self):
        client = self.login_as(self.bob)
        with mock.patch.object(si, "fetch") as f:
            response = client.get(self.url())
        self.assertEqual(404, response.status_code)
        f.assert_not_called()

    def test_text_and_image_signatures_and_bad_indexes_are_not_found(self):
        text = MailSignature.objects.create(mailbox=self.alice, name="T", text="Alice")
        client = self.login_as(self.alice)
        with mock.patch.object(si, "fetch") as f:
            for url in (self.url(text), self.url(index=2), self.url(index=99)):
                with self.subTest(url=url):
                    self.assertEqual(404, client.get(url).status_code)
        f.assert_not_called()

    def test_a_refused_fetch_is_a_502_that_names_no_url(self):
        client = self.login_as(self.alice)
        with mock.patch.object(si, "fetch", side_effect=si.SignatureImageError("blocked_address")):
            response = client.get(self.url())
        self.assertEqual(502, response.status_code)
        self.assertNotIn("netamate.com", response.content.decode())

    def test_signed_out_is_refused(self):
        with mock.patch.object(si, "fetch") as f:
            response = APIClient().get(self.url())
        self.assertIn(response.status_code, (401, 403))
        f.assert_not_called()
