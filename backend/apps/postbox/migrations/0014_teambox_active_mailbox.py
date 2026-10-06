from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("mailboxes", "0002_mailbox_kind"),
        ("postbox", "0013_extra_compact_density"),
    ]

    operations = [
        migrations.AddField(
            model_name="postboxsession",
            name="active_mailbox",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="active_postbox_sessions",
                to="mailboxes.mailbox",
            ),
        ),
        migrations.AddField(
            model_name="scheduledmessage",
            name="submission_mailbox",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="postbox_scheduled_submissions",
                to="mailboxes.mailbox",
            ),
        ),
    ]
