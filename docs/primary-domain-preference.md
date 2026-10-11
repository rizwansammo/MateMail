# Primary Domain Preference

Each MateMail organization can choose exactly one primary domain for **new mailbox creation only**.

* The `Domain.is_primary` BooleanField has a conditional database uniqueness constraint per tenant.
* On schema migration, the oldest already verified and DNS-active domain becomes the initial preference. Customers can select another in Hub → Domains at any time.
* For new organizations, the first domain to become both ownership-verified and DNS-active becomes primary automatically.
* Only verified, DNS-active domains may be newly selected. Existing primary stays selected through transient DNS warnings; it is not a mail routing mechanism.
* Rotating ownership verification clears an affected primary and chooses another eligible domain, if any. Deleting a primary via Hub does the same after the existing safe deprovision queue gate succeeds.
* Admin/Owner can POST `/api/domains/{id}/set-primary/` with a verified Hub email. Requests for domains outside the caller's tenant return 404. Other roles cannot change the preference.
* Domain list/details expose read-only `is_primary`. The Domains page shows the Primary badge and action. Mailboxes → Create defaults to that domain each time the form opens, while the select shows **only literal domain names**, with no labels or suffixes.
* Existing mailboxes, addresses, aliases, DNS, SMTP and the Native Mail Engine are unaffected. There is no tenant-wide email address rewrite.
* A verified tenant domain can remain selectable in the mailbox form despite transient DNS health warnings. Existing engine and provisioning policy checks still apply when the mailbox is submitted.

Release: migrations `domains.0007_primary_domain` and `domains.0008_unique_primary_domain` are additive. Migration 0007 adds the field and updates existing tenant domains, then commits; migration 0008 installs the partial unique index in a separate PostgreSQL transaction. This resolves the 2026-10-11 production incident `cannot CREATE INDEX ... because it has pending trigger events`. The failed prior deployment rolled back the original 0007 and did not leave the column behind. No existing mailbox data is rewritten. Verify Hub Domains and new Mailbox default with an authorized tenant after manual deployment.
