"""Stage 7A additive mailbox access metadata. No Native enforcement yet."""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("postbox", "0016_default_light_full_extra_compact"),
        ("mailboxes", "0002_mailbox_kind"),
    ]
    operations = [
        migrations.CreateModel(
            name="MailClientAccessPolicy",
            fields=[
                ("mailbox", models.OneToOneField(
                    to="mailboxes.mailbox",
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="mail_client_access_policy",
                    primary_key=True,
                    serialize=False,
                )),
                ("smtp_imap_enabled", models.BooleanField(default=True)),
                ("changed_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "postbox_mail_client_access_policy"},
        ),
    ]
