# MateMail Phase 2: Global Domain Ownership Foundation

Branch: `feat/platform-domain-ownership-phase2-20261012`

## Security boundaries

* `mail.matemail.pro` and `system.matemail.pro` are reserved as PLATFORM names in Django and in the Native Engine. This migration does not create system mailboxes, rotate DKIM, publish DNS or transfer production resources.
* Verified customer domains are backfilled into the Django registry. Pending DNS claims never become owners. Existing customer verified-domain uniqueness remains in place.
* DNS verification creates a matching registry claim within the same Django transaction. Domain provisioning checks both verified DNS ownership and the registry.
* Address allocation rejects mismatched or platform-owned domain records.
* The Native Engine uses a separate immutable reservation list and database triggers protecting Domain and Mailbox state. Legacy sender mailbox login activity remains possible.
* Native API management mutations for protected names are frozen until a separately authorized Platform System Mail management path is available in Phase 3.

## Important limitation and next step

The first Phase 2 slice does NOT implement automatic domain transfer or release.
A deleted customer domain keeps its registry claim until an explicitly audited
cleanup mechanism proves Native Engine domain, DKIM, mailbox and queued task
state absent. Never manually delete a claim without performing that review.

The Native Engine does not access the Django database. Future system domain
creation must register ownership centrally, then atomically reconcile a
protection reservation with the Native API; an unprotected name must never be
provisioned as a platform mailbox. These are Phase 3 requirements.

## Deployment restrictions

Do not deploy this branch or its SQL migration during the DNS Canary observation.
Review existing domain collisions and old queued provisioning jobs first.
Verify backup and restore health, including the unresolved Restic lock. Apply
Django and Native API changes together through a controlled rollout only after
explicit authorization and tested rollback.

## Validation

Run Django migration check and backend regression tests in an isolated test
database; run Native Engine migration and protection tests against disposable
PostgreSQL, not the production engine. A successful branch commit alone is not
a production validation.

## Additional guards

Native DKIM rotation/deletion, aliases, Forward Groups, forwarding and
mailbox submission limit changes must also respect the platform reservation.
A verified tenant must never be able to bypass the registry through a stale
asynchronous delete task. While a customer Domain row still exists, engine
deprovisioning is blocked; the cleanup path must be checked for queue races
before this foundation can be approved for deployment.

## Outstanding offboarding race

The current domain deletion endpoint queues engine cleanup before committing the
Django Domain deletion. Phase 2 deliberately does not insert a new test of live
Domain existence into the Celery task because a fast worker could see the still
present record and incorrectly skip essential DKIM cleanup. A source ownership
token bound to queued cleanup plus durable pending-deletion state must be
implemented and regression-tested before enabling automated claim release.

## Protected infrastructure hostnames

The global namespace also reserves known MateMail service hosts:
`mx`, `platform`, `portal`, `postbox`, `custom`,
`autodiscover`, `admin` and `www` under `matemail.pro`.
These are reservations only, not mail-provisioned domains. Do not
reserve the `matemail.pro` apex: it remains a verified organization domain.
