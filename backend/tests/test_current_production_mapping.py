"""Regression guard for the consolidated MateMail production identity and paths.

The original .online nginx files remain as intentionally marked historical artifacts.
Do not replace or move the immutable legacy migration tests here.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_active_deploy_workflow_dumps_to_existing_backup_tree():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "install -d -m 0700 /opt/MateMail/backup/pre-deploy" in workflow
    assert "/opt/MateMail/backup/pre-deploy/pre-deploy-" in workflow
    assert "/opt/MateMail/backups/pre-deploy-" not in workflow


def test_app_defaults_do_not_restore_retired_mail_identity():
    dns = (ROOT / "backend/apps/dnshealth/services.py").read_text()
    frontend = (ROOT / "frontend/app/layout.tsx").read_text()
    assert 'getattr(settings, "MAIL_HOSTNAME", "mx.matemail.pro")' in dns
    assert 'getattr(settings, "SPF_INCLUDE_DOMAIN", "_spf.matemail.pro")' in dns
    assert "https://matemail.pro" in frontend


def test_platform_backup_message_names_both_independent_backup_timers():
    api = (ROOT / "backend/apps/platform_admin/ops_views.py").read_text()
    assert "/opt/MateMail/backup/matemail-restore.sh" in api
    assert "matemail-backup.timer" in api
    assert "mateserver-backup.timer" in api


def test_legacy_nginx_templates_are_explicitly_archived():
    templates = list((ROOT / "deploy/nginx").glob("*.matemail.online*.conf"))
    assert len(templates) == 8
    for template in templates:
        assert template.read_text().startswith("# RETIRED ARCHIVE - DO NOT INSTALL")
