"""Deployment and UI regression contracts for PostBox filters and safe deletion."""
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


def source(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


class SieveDeploymentContractTest(SimpleTestCase):
    def test_native_dovecot_activates_bundled_pigeonhole_on_lmtp(self):
        dovecot = source("deploy", "native-engine", "dovecot", "dovecot.conf")
        self.assertIn("protocols = imap lmtp sieve", dovecot)
        self.assertIn("sieve_script personal {", dovecot)
        self.assertIn("path = ~/sieve", dovecot)
        self.assertIn("active_path = ~/.dovecot.sieve", dovecot)
        self.assertIn("protocol lmtp {", dovecot)
        self.assertIn("sieve = yes", dovecot)
        self.assertIn("service managesieve-login {", dovecot)
        self.assertIn("port = 4190", dovecot)
        self.assertIn("ssl = required", dovecot)

    def test_gateway_forwards_managesieve_privately_without_public_port(self):
        gateway = source("deploy", "native-engine", "gateway", "haproxy.cfg")
        native_compose = source("deploy", "native-engine", "docker-compose.yml")
        app_compose = source("deploy", "docker-compose.yml")
        self.assertIn("frontend managesieve\n    bind :4190", gateway)
        self.assertIn("server dovecot dovecot:4190", gateway)
        self.assertNotIn(":4190:4190", native_compose)
        self.assertIn("POSTBOX_SIEVE_STARTTLS: ${POSTBOX_SIEVE_STARTTLS:-True}", app_compose)
        self.assertIn("POSTBOX_SIEVE_TLS_SERVER_NAME: ${POSTBOX_SIEVE_TLS_SERVER_NAME:-mx.matemail.online}", app_compose)

    def test_no_new_engine_network_or_image_build_is_needed(self):
        image = source("deploy", "native-engine", "images", "dovecot", "Dockerfile")
        native = source("deploy", "native-engine", "docker-compose.yml")
        self.assertIn("FROM dovecot/dovecot:2.4.1@", image)
        self.assertIn("matemail_engine_link:", native)

    def test_folder_warning_is_independent_of_mail_rules_transport(self):
        backend = source("backend", "apps", "postbox", "views_mail.py")
        api = source("frontend", "lib", "postbox-api.ts")
        layout = source("frontend", "app", "postbox", "(app)", "layout.tsx")
        self.assertIn('"code": "folder_not_empty"', backend)
        self.assertIn('"code": "folder_has_rules"', backend)
        self.assertIn('connection.delete_folder(name)', backend)
        delete_section = backend.split("    def delete(self, request, name: str):", 1)[1].split("# ── listing", 1)[0]
        self.assertNotIn("connection.move(", delete_section)
        self.assertNotIn("_sync_sieve(", delete_section)
        self.assertIn("folderDeleteCheck:", api)
        self.assertIn("Folder Contains Emails", layout)
        self.assertIn("Please manually move all emails", layout)
        self.assertIn("Open Folder", layout)
