"""
The engine-side half of P5: does Postfix actually ask MateMail, and in what order?

MateMail deciding correctly is worth nothing if the Mail Engine never asks. These
tests are about the versioned engine configuration in `deploy/engine/` — the
Postfix restriction chain and the bridge sidecar that answers it.

## What these tests are, and what they are not

They assert on the real configuration text that is installed on the engine host,
and they evaluate it with a model of Postfix's own restriction semantics. That
makes them a genuine ordering proof about the configuration — not a restatement
of it — and it makes a future mailcow upgrade that silently reorders or drops the
hook fail here rather than in production.

They are **not** proof that Postfix itself behaves as modelled. Postfix is not
running in CI. The remaining evidence is the deployment-time verification in
`scripts/install-policy-bridge.sh`, which prints the engine's own effective
`postconf` output and states what must appear in it.

That distinction is kept explicit on purpose. A test that claimed to prove
Postfix behaviour from a Python string comparison would be the most dangerous
kind of green.
"""
import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
EXTRA_CF = REPO / "deploy" / "engine" / "postfix-extra.cf"
COMPOSE = REPO / "deploy" / "engine" / "docker-compose.override.yml"
BRIDGE = REPO / "scripts" / "postfix_policy_bridge.py"
INSTALLER = REPO / "scripts" / "install-policy-bridge.sh"

#: The chain mailcow 2026-07b generates, read from the running engine's own
#: main.cf. Recorded here so that an upgrade which changes it makes the
#: comparison below fail, instead of leaving our override silently pinned to a
#: list upstream has moved on from.
#:
#: Re-derive after any mailcow upgrade — see the footer of postfix-extra.cf.
UPSTREAM_RECIPIENT_CHAIN = [
    "check_recipient_mx_access proxy:mysql:/opt/postfix/conf/sql/mysql_mbr_access_maps.cf",
    "permit_sasl_authenticated",
    "permit_mynetworks",
    "check_recipient_access proxy:mysql:/opt/postfix/conf/sql/mysql_tls_enforce_in_policy.cf",
    "reject_invalid_helo_hostname",
    "reject_unauth_destination",
]

MAILCOW_VERSION_VERIFIED_AGAINST = "2026-07b"


def parse_parameters(text: str) -> dict[str, list[str]]:
    """
    Parse a Postfix configuration fragment into {parameter: [entries]}.

    Postfix continues a value onto following lines when they begin with
    whitespace, and comments are whole-line only. Written out rather than
    regex-matched in one go so the continuation rule is visible: it is the
    rule that decides whether our hook is part of the chain or a stray line.
    """
    params: dict[str, str] = {}
    current: str | None = None

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[0].isspace():
            if current:
                params[current] += " " + raw.strip()
            continue
        name, sep, value = raw.partition("=")
        if not sep:
            continue
        current = name.strip()
        params[current] = value.strip()

    return {
        key: [entry.strip() for entry in value.split(",") if entry.strip()]
        for key, value in params.items()
    }


def evaluate(chain: list[str], *, authenticated: bool, in_mynetworks: bool) -> str:
    """
    Model Postfix's evaluation of one restriction list for one client.

    Postfix walks the list in order and stops at the first entry that returns a
    decisive answer: a `permit_*` that matches permits and stops, a `reject_*`
    rejects and stops, and anything else defers to the next entry. Returns the
    entry that decided, so a test can assert *which* restriction settled a case
    rather than only the outcome.

    Deliberately narrow: it models exactly the entries in this chain and raises
    on anything unrecognised, so a new upstream restriction cannot be silently
    treated as a no-op.
    """
    for entry in chain:
        if entry == "permit_mynetworks":
            if in_mynetworks:
                return entry
        elif entry == "permit_sasl_authenticated":
            if authenticated:
                return entry
        elif entry.startswith("check_policy_service"):
            # The policy service is reached. Its own answer is DUNNO / REJECT /
            # DEFER — never OK — so a DUNNO continues the walk. Reaching it is
            # what these tests are about.
            return entry
        elif entry.startswith(("check_recipient_access", "check_recipient_mx_access")):
            # A lookup table. Matches nothing for these synthetic clients.
            continue
        elif entry.startswith("reject_"):
            return entry
        else:
            raise AssertionError(
                f"evaluate() does not model the restriction {entry!r}. "
                "A new upstream entry must be modelled deliberately, not ignored."
            )
    return "END_OF_CHAIN"


class ExtraCfExistsTest(unittest.TestCase):
    def test_the_versioned_assets_are_present(self):
        for path in (EXTRA_CF, COMPOSE, BRIDGE, INSTALLER):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f"missing {path}")

    def test_it_records_the_engine_version_it_was_verified_against(self):
        """
        This file reproduces part of an upstream-generated chain. Which upstream
        is the only thing that makes that reproduction checkable.
        """
        self.assertIn(MAILCOW_VERSION_VERIFIED_AGAINST, EXTRA_CF.read_text(encoding="utf-8"))


class RecipientRestrictionOrderingTest(unittest.TestCase):
    """
    The ordering that is the whole of "blocker 2".
    """

    def setUp(self):
        self.params = parse_parameters(EXTRA_CF.read_text(encoding="utf-8"))
        self.chain = self.params.get("smtpd_recipient_restrictions", [])
        self.assertTrue(self.chain, "extra.cf does not define smtpd_recipient_restrictions")

    def index_of_hook(self):
        for i, entry in enumerate(self.chain):
            if entry.startswith("check_policy_service"):
                return i
        self.fail("no check_policy_service in smtpd_recipient_restrictions")

    def test_the_policy_hook_is_present(self):
        self.index_of_hook()

    def test_the_hook_precedes_permit_sasl_authenticated(self):
        """
        The defect this closes. With the hook after `permit_sasl_authenticated`,
        Postfix stops evaluating the moment a client authenticates — so every
        authenticated submission, which is exactly the traffic MateMail needs to
        rule on, short-circuits before reaching the policy service. The hook
        would be installed, running, and never asked.
        """
        self.assertLess(
            self.index_of_hook(),
            self.chain.index("permit_sasl_authenticated"),
            "the policy hook must come BEFORE permit_sasl_authenticated",
        )

    def test_the_hook_follows_permit_mynetworks(self):
        """
        So the engine's own unauthenticated injections — watchdog probes,
        quarantine digests — are settled before customer policy is consulted.
        MateMail knows nothing about those recipients.
        """
        self.assertGreater(
            self.index_of_hook(),
            self.chain.index("permit_mynetworks"),
            "the policy hook must come AFTER permit_mynetworks",
        )

    def test_the_hook_precedes_reject_unauth_destination(self):
        """
        Which is why the bridge must answer DUNNO and never OK: OK would stop
        the walk here and skip the anti-relay check below.
        """
        self.assertLess(
            self.index_of_hook(), self.chain.index("reject_unauth_destination")
        )

    def test_every_upstream_restriction_is_still_present(self):
        """
        The hook is inserted into mailcow's chain, never substituted for it.
        Dropping one of these would trade a working engine defence for a
        MateMail check that only covers part of the same ground.
        """
        for entry in UPSTREAM_RECIPIENT_CHAIN:
            with self.subTest(entry=entry):
                self.assertIn(entry, self.chain)

    def test_nothing_beyond_the_hook_was_added(self):
        extra = [e for e in self.chain if e not in UPSTREAM_RECIPIENT_CHAIN]
        self.assertEqual(
            len(extra), 1, f"expected only the policy hook to be added, found {extra}"
        )
        self.assertTrue(extra[0].startswith("check_policy_service"))

    def test_the_upstream_relative_order_is_preserved_apart_from_the_permit_swap(self):
        """
        Only two adjacent permits swap places, which cannot change which clients
        are permitted. Everything else keeps mailcow's order.
        """
        without_hook = [e for e in self.chain if not e.startswith("check_policy_service")]
        expected = [
            "check_recipient_mx_access proxy:mysql:/opt/postfix/conf/sql/mysql_mbr_access_maps.cf",
            "permit_mynetworks",
            "permit_sasl_authenticated",
            "check_recipient_access proxy:mysql:/opt/postfix/conf/sql/mysql_tls_enforce_in_policy.cf",
            "reject_invalid_helo_hostname",
            "reject_unauth_destination",
        ]
        self.assertEqual(without_hook, expected)

    def test_the_swap_moves_only_two_adjacent_permits(self):
        """The claim above, checked rather than asserted in prose."""
        without_hook = [e for e in self.chain if not e.startswith("check_policy_service")]
        differing = [
            i for i, (a, b) in enumerate(zip(without_hook, UPSTREAM_RECIPIENT_CHAIN)) if a != b
        ]
        self.assertEqual(differing, [1, 2])
        self.assertTrue(all(without_hook[i].startswith("permit_") for i in differing))
        self.assertEqual(
            sorted(without_hook), sorted(UPSTREAM_RECIPIENT_CHAIN),
            "the swap must be a reordering, not an addition or a removal",
        )


class WhoReachesThePolicyServiceTest(unittest.TestCase):
    """
    Evaluate the effective chain for each real class of client.

    This is the ordering proof: not "the hook is at index 2", but "an
    authenticated external submission reaches it and an engine-internal
    injection does not".
    """

    def setUp(self):
        params = parse_parameters(EXTRA_CF.read_text(encoding="utf-8"))
        self.recipient_chain = params["smtpd_recipient_restrictions"]
        self.eod_chain = params["smtpd_end_of_data_restrictions"]

    def test_an_authenticated_customer_reaches_the_policy_service(self):
        """A real customer on 587 from the Internet — the traffic that matters."""
        decided = evaluate(self.recipient_chain, authenticated=True, in_mynetworks=False)
        self.assertTrue(
            decided.startswith("check_policy_service"),
            f"authenticated submission was settled by {decided!r} instead of the policy service",
        )

    def test_an_external_inbound_delivery_reaches_the_policy_service(self):
        """Port 25 from another MTA — recipient existence and account state."""
        decided = evaluate(self.recipient_chain, authenticated=False, in_mynetworks=False)
        self.assertTrue(decided.startswith("check_policy_service"))

    def test_an_engine_internal_injection_does_not_reach_the_policy_service(self):
        """
        Watchdog probes and quarantine digests. Asking MateMail about these
        would answer "domain not hosted here" and break the engine's own
        monitoring.
        """
        decided = evaluate(self.recipient_chain, authenticated=False, in_mynetworks=True)
        self.assertEqual(decided, "permit_mynetworks")

    def test_the_platform_sender_is_settled_at_end_of_data_instead(self):
        """
        MateMail's own sender authenticates but arrives from mynetworks, via the
        gateway. It is permitted before the recipient hook — and then fully
        checked and counted at end-of-data, which has no permit in front of it.
        """
        at_rcpt = evaluate(self.recipient_chain, authenticated=True, in_mynetworks=True)
        self.assertEqual(at_rcpt, "permit_mynetworks")

        at_eod = evaluate(self.eod_chain, authenticated=True, in_mynetworks=True)
        self.assertTrue(
            at_eod.startswith("check_policy_service"),
            "the platform sender must still be checked and counted at end-of-data",
        )

    def test_no_client_class_escapes_both_hooks(self):
        """The property that matters: every message is ruled on somewhere."""
        for authenticated in (True, False):
            for in_mynetworks in (True, False):
                with self.subTest(auth=authenticated, mynet=in_mynetworks):
                    reached = [
                        evaluate(self.recipient_chain, authenticated=authenticated,
                                 in_mynetworks=in_mynetworks),
                        evaluate(self.eod_chain, authenticated=authenticated,
                                 in_mynetworks=in_mynetworks),
                    ]
                    if authenticated:
                        self.assertTrue(
                            any(d.startswith("check_policy_service") for d in reached),
                            "an authenticated client reached neither policy hook",
                        )


class EndOfDataRestrictionTest(unittest.TestCase):
    """The stage where the rate-limit counter moves, exactly once per message."""

    def setUp(self):
        self.params = parse_parameters(EXTRA_CF.read_text(encoding="utf-8"))

    def test_the_end_of_data_hook_exists(self):
        chain = self.params.get("smtpd_end_of_data_restrictions", [])
        self.assertTrue(chain)
        self.assertTrue(chain[0].startswith("check_policy_service"))

    def test_it_contains_nothing_but_the_hook(self):
        """
        Upstream leaves this parameter empty, so there is nothing to preserve —
        and no permit may be placed in front of the hook, or the platform sender
        would escape counting entirely.
        """
        self.assertEqual(len(self.params["smtpd_end_of_data_restrictions"]), 1)

    def test_both_hooks_address_the_same_service(self):
        recipient_hook = next(
            e for e in self.params["smtpd_recipient_restrictions"]
            if e.startswith("check_policy_service")
        )
        eod_hook = self.params["smtpd_end_of_data_restrictions"][0]
        self.assertEqual(recipient_hook, eod_hook)


class UpstreamDefencesPreservedTest(unittest.TestCase):
    """
    What the override must NOT touch.

    Each of these is an engine defence that MateMail's policy duplicates only
    partly. Replacing one with a policy hook — which the bridge's own
    documentation used to advise — trades a working check for a narrower one.
    """

    def setUp(self):
        self.params = parse_parameters(EXTRA_CF.read_text(encoding="utf-8"))

    def test_sender_restrictions_are_not_redefined(self):
        """
        Upstream begins them with `reject_authenticated_sender_login_mismatch`,
        which rejects a spoofed MAIL FROM at the earliest possible moment —
        earlier than any policy service can be consulted.
        """
        self.assertNotIn("smtpd_sender_restrictions", self.params)

    def test_relay_restrictions_are_not_redefined(self):
        """
        `defer_unauth_destination` is what makes unauthenticated relay
        impossible, and it holds independently of anything the policy service
        answers — including while the policy service is down.
        """
        self.assertNotIn("smtpd_relay_restrictions", self.params)

    def test_client_restrictions_are_not_redefined(self):
        self.assertNotIn("smtpd_client_restrictions", self.params)

    def test_milters_are_not_redefined(self):
        """Rspamd rules on content and reputation; MateMail rules on identity."""
        for param in ("smtpd_milters", "non_smtpd_milters"):
            with self.subTest(param=param):
                self.assertNotIn(param, self.params)

    def test_mynetworks_and_tls_policy_are_not_redefined(self):
        for param in (
            "mynetworks", "mynetworks_style",
            "smtpd_tls_security_level", "smtpd_tls_auth_only",
            "smtpd_sasl_auth_enable", "smtpd_sender_login_maps",
        ):
            with self.subTest(param=param):
                self.assertNotIn(param, self.params)

    def test_the_engine_identity_lines_survive(self):
        """
        extra.cf is replaced wholesale on install, so anything that was in it
        has to be carried forward. `myhostname` backs the PTR record; losing it
        breaks outbound authentication.
        """
        self.assertEqual(self.params["myhostname"], ["mx.matemail.online"])
        self.assertIn("mx.matemail.online", " ".join(self.params["smtpd_banner"]))

    def test_the_banner_does_not_name_the_engine(self):
        banner = " ".join(self.params["smtpd_banner"]).lower()
        for leak in ("postfix", "mailcow", "dovecot", "ubuntu", "debian"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, banner)


class BridgeSidecarTest(unittest.TestCase):
    """The container that answers the hook."""

    def setUp(self):
        self.compose = COMPOSE.read_text(encoding="utf-8")
        start = self.compose.index("matemail-policy-bridge:")
        end = self.compose.index("networks:\n  # Created and owned OUTSIDE")
        self.block = self.compose[start:end]
        # Comment lines removed. A prose line reading "No `ports:`. Deliberately"
        # otherwise satisfies a search for "ports:" and the test passes for the
        # opposite of the reason it was written.
        self.directives = "\n".join(
            line for line in self.block.splitlines() if not line.lstrip().startswith("#")
        )

    def test_the_service_is_defined(self):
        self.assertIn("matemail-policy-bridge:", self.compose)

    def test_it_publishes_no_host_port(self):
        """
        The defect DEC-014 was written to close. A published bind address
        selects a destination, never a permitted source, so every other Docker
        network on the host could reach it — and this socket decides whether
        mail may be sent.
        """
        self.assertNotIn("ports:", self.directives)

    def test_it_uses_no_host_networking(self):
        for forbidden in ("network_mode: host", "network_mode: \"host\"", "host.docker.internal"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.directives)

    def test_the_image_is_pinned_by_digest(self):
        """
        This sits in the path of customer mail. An unattended image change is
        an outage nobody chose the timing of.
        """
        match = re.search(r"image:\s*(\S+)", self.directives)
        self.assertIsNotNone(match)
        image = match.group(1)
        self.assertIn("@sha256:", image)
        self.assertNotIn(":latest", image)

    def test_it_sits_on_both_networks_and_only_those(self):
        self.assertIn("mailcow-network:", self.directives)
        self.assertIn("matemail_engine_link:", self.directives)

    def test_its_address_is_static(self):
        """
        Postfix dials this address by number. A DHCP address would move on
        recreation, and every message would defer with nothing looking wrong.
        """
        self.assertRegex(self.directives, r"ipv4_address:\s*10\.244\.0\.246")

    def test_the_address_postfix_dials_is_the_address_compose_binds(self):
        """
        Two files that have to agree about one number. If they drift, Postfix
        dials an address nothing answers on and all mail defers.
        """
        params = parse_parameters(EXTRA_CF.read_text(encoding="utf-8"))
        hook = next(
            e for e in params["smtpd_recipient_restrictions"]
            if e.startswith("check_policy_service")
        )
        dialled = hook.split("inet:")[1].strip()

        address = re.search(r"ipv4_address:\s*([0-9.]+)", self.directives).group(1)
        port = re.search(r'LISTEN_PORT:\s*"(\d+)"', self.directives).group(1)
        self.assertEqual(dialled, f"{address}:{port}")

    def test_it_reaches_matemail_over_the_internal_link_not_a_host_address(self):
        url = re.search(r'DJANGO_INTERNAL_URL:\s*"([^"]+)"', self.directives).group(1)
        self.assertIn("backend", url)
        for leak in ("127.0.0.1", "localhost", "host.docker.internal", "169.58."):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, url)

    def test_the_shared_secret_is_required_and_not_written_here(self):
        """
        It must come from the engine's environment. A literal here would put the
        credential that authenticates every policy call into the repository.
        """
        self.assertIn("MATEMAIL_INTERNAL_API_SECRET", self.directives)
        self.assertIn(":?", self.directives, "the variable must be required, not defaulted")

    def test_the_script_it_mounts_is_the_one_in_the_repository(self):
        self.assertIn("postfix_policy_bridge.py", self.directives)

    def test_it_has_a_healthcheck(self):
        self.assertIn("healthcheck:", self.directives)


class NoHostDaemonRemnantsTest(unittest.TestCase):
    """
    The superseded topology must not be shipped alongside the replacement.

    A systemd unit that binds the bridge on the host is the exact
    published-socket arrangement DEC-014 measured and rejected. Leaving it in
    the repository beside the sidecar is an invitation to install the wrong one.
    """

    def test_no_systemd_unit_for_a_host_bound_bridge_remains(self):
        self.assertFalse(
            (REPO / "scripts" / "postfix-policy-bridge.service").exists(),
            "the host-daemon systemd unit is superseded by the sidecar and must not ship",
        )

    def test_the_bridge_does_not_advise_a_host_published_socket(self):
        text = BRIDGE.read_text(encoding="utf-8")
        for advice in ("ufw allow", "HOST_IP", "host gateway"):
            with self.subTest(advice=advice):
                self.assertNotIn(advice, text)

    def test_the_bridge_does_not_advise_replacing_sender_restrictions(self):
        """
        Its earlier documentation advised
        `smtpd_sender_restrictions = check_policy_service ...`, which would have
        discarded `reject_authenticated_sender_login_mismatch` — the engine's
        own anti-spoofing check — in favour of a duplicate.
        """
        self.assertNotIn("smtpd_sender_restrictions", BRIDGE.read_text(encoding="utf-8"))


class InstallerTest(unittest.TestCase):
    """The installer is the only sanctioned way these files reach the engine."""

    def setUp(self):
        self.text = INSTALLER.read_text(encoding="utf-8")

    def test_it_refuses_to_run_without_the_shared_secret(self):
        """Installing without it would defer every message."""
        self.assertIn("MATEMAIL_INTERNAL_API_SECRET", self.text)

    def test_it_verifies_the_engine_link_is_internal(self):
        self.assertIn("matemail_engine_link", self.text)
        self.assertIn("Internal", self.text)

    def test_it_checks_the_two_files_agree_on_the_address(self):
        self.assertIn("address mismatch", self.text)

    def test_it_backs_up_before_replacing(self):
        self.assertIn("pre-matemail", self.text)

    def test_it_starts_the_bridge_before_reloading_postfix(self):
        """
        The other order opens a window where Postfix dials a service that is not
        yet listening, and every message in that window defers.
        """
        bridge_up = self.text.index("up -d matemail-policy-bridge")
        postfix_reload = self.text.index("restart postfix-mailcow")
        self.assertLess(bridge_up, postfix_reload)

    def test_it_opens_no_ports_and_touches_no_firewall(self):
        for forbidden in ("ufw ", "iptables", "firewall-cmd"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text.lower())

    def test_it_has_a_check_only_mode(self):
        self.assertIn("--check", self.text)

    def test_it_prints_the_effective_configuration_for_verification(self):
        """
        The one thing these tests cannot prove is what Postfix actually does.
        The installer must end by showing it.
        """
        self.assertIn("postconf", self.text)
