# MateMail collaboration release — production rollout

This runbook is for the TeamBox / Forward Group / Delegation release.

The order is intentional:

1. **Native Engine first**
2. **MateMail application second**

The application deployment workflow enforces that order and refuses to touch
MateMail when the running Native Engine is not collaboration-ready.

## 1. Preconditions

Before production work:

- the collaboration branch has a green CI run;
- the release has been merged to `main`;
- the exact release commit SHA is known;
- the normal MateMail database backup path is healthy;
- the Native Engine database and configuration have a recoverable backup;
- no one is manually editing mail routing state during the cutover.

Do not deploy from an unmerged feature-branch SHA.

## 2. Build the Native Engine release

Phase G changes two Native Engine image components:

- `api` — carries schema migration 006 and Forward Group provisioning;
- `postfix` — carries the Forward Group posting-policy control code.

Use the manual **Native Engine images** GitHub Actions workflow for the exact
merged release. Build at least `api` and `postfix` (building `all` is also
valid). Pin the resulting immutable digests in the Native Engine production
`.env`.

The Postfix configuration is bind-mounted rather than baked into the image, so
the production Native Engine configuration directory must also receive the
same release's `deploy/native-engine/postfix/main.cf` and the matching
`deploy.sh`. Preserve the root-only production `.env`; never replace it with
a repository template.

Production runtime is under `/opt/MateMail/engine/deploy/native-engine/`. Use the
existing Native Engine deployment process, not bare `docker compose up -d`.

## 3. Deploy the Native Engine

From the production Native Engine deployment directory, use:

```bash
./deploy.sh pull
```

The API applies schema migration 006 under its migration advisory lock. The
Postfix configuration hash causes the Postfix container to be recreated when
the RCPT policy configuration changed.

Do not run `docker compose down`.

## 4. Verify Native collaboration readiness

All Native Engine services should be healthy.

The Native API authenticated `/ready` response must report:

- `ready: true`;
- `schema_version >= 6`;
- `capabilities.mailbox_sender_authorization: true`;
- `capabilities.forward_groups: true`;
- `capabilities.forward_group_sender_policy: true`;
- `capabilities.collaboration_version >= 1`.

Running Postfix must have the Forward Group recipient-policy hook:

```
check_policy_service inet:127.0.0.1:10032
```

and its control-plane image must contain `forward_group_verdict`.

The MateMail application deployment performs these same checks again and
fails before changing the application if any of them are missing.

## 5. Deploy MateMail

Only after the Native Engine checks pass, run the manual **Deploy to
MateServer** workflow with:

- the exact green merged commit SHA;
- confirmation text `MateServer`.

The workflow:

- verifies the release images exist;
- verifies the private engine-link network;
- verifies Native collaboration readiness;
- validates the exact release Compose file;
- backs up the current MateMail database;
- runs Django migrations;
- runs `python manage.py collaboration_preflight`;
- starts backend/worker/beat only when both migration and preflight succeed;
- waits for backend health and retains the existing rollback path.

## 6. What the collaboration preflight validates

The preflight is read-only. It checks:

- every Mailbox, TeamBox, Alias and Forward Group has the correct
  `AddressClaim`;
- no orphan address claims exist;
- TeamBox and Delegation grants satisfy tenant/type/permission invariants;
- active Forward Groups have members;
- selected Forward Group senders are valid personal mailboxes in the same
  organization;
- no direct Forward Group → member → Forwarding → same Forward Group loop
  exists.

A failure is a stop condition. Inspect and correct the specific data problem;
do not bypass the command or edit migration history to make the deployment
continue.

## 7. Post-deploy smoke checks

After the application is healthy, verify with a small internal test set:

- personal PostBox login still opens the personal mailbox;
- an authorized TeamBox can be opened without a TeamBox password;
- TeamBox Read/Manage/Send permissions behave separately;
- a delegated mailbox opens from the delegate's own login;
- revoking Delegation removes access without affecting the delegate's personal
  mailbox;
- Send As uses the shared/target address while SMTP authentication remains the
  personal actor;
- Send on behalf produces the expected `Sender` semantics;
- a Forward Group distributes one message to each configured member;
- a restricted Forward Group rejects a sender that is not authenticated as an
  allowed personal mailbox;
- existing Alias and mailbox Forwarding behavior remains separate and intact.

## 8. Rollback boundary

The Django collaboration migrations are additive, but production rollback must
still be treated as a release operation rather than manually unapplying
migrations.

If the application release fails, use the existing deployment rollback path.
Do not roll the Native Engine back below schema v6 while a collaboration-aware
MateMail release is running.

If a Native Engine rollback is ever required, restore the compatible engine
configuration/images and database backup as one reviewed operation before
attempting an older application release.
