"""Fixed system role activation cannot accept caller-supplied identities or passwords."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import platform_roles
from validation import ValidationError

ENV = {
    "NATIVE_API_SECRET": "ordinary-secret-at-least-32-000000000",
    "NATIVE_DOVECOT_POLICY_SECRET": "dovecot-secret-at-least-32-111111",
    "NATIVE_DB_PASSWORD": "general-db-secret-at-least-32-222222",
    "NATIVE_PLATFORM_SECRET": "independent-platform-secret-at-least-32",
    "NATIVE_PLATFORM_DB_PASSWORD": "independent-platform-db-secret-333333",
    "NATIVE_PLATFORM_OPERATIONS_ENABLED": "True",
    "NATIVE_SYSTEM_REMAINING_ACTIVATION_ENABLED": "True",
    "NATIVE_SYSTEM_REMAINING_ROLLBACK_ENABLED": "True",
}


class RemainingRolesTests(unittest.TestCase):
    def test_activation_is_off_by_default(self):
        with mock.patch.dict(os.environ,{**ENV,"NATIVE_SYSTEM_REMAINING_ACTIVATION_ENABLED":"False"},clear=True):
            with mock.patch.object(platform_roles.db,"connect_platform") as db:
                with self.assertRaises(PermissionError):
                    platform_roles.activate_remaining(platform_roles.ACTIVATE)
                db.assert_not_called()

    def test_only_fixed_request_allowed(self):
        with mock.patch.dict(os.environ,ENV,clear=True):
            with mock.patch.object(platform_roles.db,"connect_platform") as db:
                for body in (None,{},[],{**platform_roles.ACTIVATE,"role":"owner"},
                             {"domain":"mail.matemail.pro","operation":"activate_remaining_system_roles"}):
                    with self.subTest(body=body),self.assertRaises(ValidationError):
                        platform_roles.activate_remaining(body)
                db.assert_not_called()

    def test_uses_only_local_secrets_and_never_returns_them(self):
        with mock.patch.dict(os.environ,ENV,clear=True):
            with mock.patch.object(platform_roles.platform_canary,"_check_signer"):
                with mock.patch.object(platform_roles,"_read_role_secret",return_value="private-server-secret"):
                    with mock.patch.object(platform_roles.passwords,"hash_password",
                                           return_value="{BLF-CRYPT}$2b$10$synthetic") as hashed:
                        with mock.patch.object(platform_roles.db,"connect_platform") as db:
                            cur=db.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                            cur.fetchone.return_value=({"active_mailboxes":5,"sender_switched":False},)
                            result=platform_roles.activate_remaining(platform_roles.ACTIVATE)
        self.assertEqual(result["active_mailboxes"],5)
        self.assertEqual(hashed.call_count,4)
        self.assertEqual(cur.execute.call_args.args[0],
                         "SELECT public.platform_activate_remaining_system_roles(%s,%s,%s,%s)")
        self.assertNotIn("private-server-secret",str(result))

    def test_rollback_gated_separately(self):
        with mock.patch.dict(os.environ,{**ENV,"NATIVE_SYSTEM_REMAINING_ROLLBACK_ENABLED":"False"},clear=True):
            with mock.patch.object(platform_roles.db,"connect_platform") as db:
                with self.assertRaises(PermissionError):
                    platform_roles.deactivate_remaining(platform_roles.DEACTIVATE)
                db.assert_not_called()

    def test_secrets_are_private_and_role_names_fixed(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(platform_roles.pathlib,"Path",wraps=pathlib.Path) as _unused:
                pass
            with mock.patch.object(platform_roles,"ROLES",("noreply",)):
                with self.assertRaises(PermissionError):
                    platform_roles._read_role_secret("customer")

