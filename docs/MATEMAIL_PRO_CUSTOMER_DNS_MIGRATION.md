> **CURRENT DOCUMENTATION NOTICE (2026-10-09):** Historical transition plan for `.pro` DNS. Cutover actions have already taken place; do not run destructive/retirement tasks from this plan against current production.
> Authoritative current references: [Architecture](ARCHITECTURE.md),
> [Deployment](DEPLOYMENT.md), and [Backup/Azure DR](BACKUP_RESTORE.md).
> Sections below may describe historical migration states or retired domains.

---

# MateMail .online → .pro: customer DNS migration register

Status: **planned / not yet activated**. This checklist is part of the migration acceptance criteria, not an instruction to switch DNS during Phase C.

## Per-tenant inventory

For **every** tenant domain, record the actual authoritative DNS responses (not assumptions), current expected values in Hub, owner/registrar, responsible contact, planned cutover time, and verified-after status. Handle multiple domains per organization separately. Do not recreate or delete mailboxes or tenant identities.

| Record / endpoint | Existing value to inspect | Future target / rule | Cutover gate |
| --- | --- | --- | --- |
| MX | MX records resolving to `mx.matemail.online` | `mx.matemail.pro` once mail transport, TLS, reverse DNS and inbound tests pass | Phase D |
| SPF TXT | `include:_spf.matemail.online` if actually present | `include:_spf.matemail.pro` only **after** publishing and verifying the new SPF include record and end-to-end authorization | Phase D |
| DKIM TXT | Tenant selector keys | **Preserve** existing tenant signing identity/key unless intentionally rotated | Keep |
| DMARC TXT | Tenant DMARC policy and report destinations | **Preserve** unless sending identity/reporting changes require review | Keep |
| Custom-host CNAME | `custom.matemail.online` if used | `custom.matemail.pro` only once provisioner and Caddy/Nginx verify both old and new aliases during transition | Phase E |
| Autodiscover / SRV | Existing customer client-discovery records | Update only where real records exist and new services work | Phase D |
| Existing IMAP/SMTP clients | Client settings (not DNS zone records) | New `mx.matemail.pro` hostname after end-to-end TLS & authentication verification | Phase D |
| Customer site A records | Non-mail website infrastructure | **No change** | N/A |

## Mandatory Hub functionality

1. Show NEW canonical DNS instructions for newly added customer domains only after Phase D infrastructure readiness.
2. For existing tenant domains, show migration status per record: **Old detected**, **New verified**, **Missing / mismatched**, **Not applicable**; keep existing domains operational.
3. MX: compare answers against legacy and new valid targets during transition; do not report existing working mail as inactive solely because an old MX remains.
4. SPF: parse all TXT records and confirm authorization; do not blindly replace multi-provider SPF records or create duplicate SPF TXT records.
5. DKIM/DMARC: retain tenant keys and policy; validate selector/DMARC alignment, do not demand changes if valid.
6. Custom-domain verification/provisioning: accept old valid CNAMEs temporarily; never break existing custom PostBox/Portal URLs; preserve tenant hostname ownership and certificate state.
7. Capture verification timestamps and useful instructions, avoid false success until DNS and functionality checks pass.
8. Prevent any advice to delete legacy MX/CNAME until every tenant/client dependency is migrated.
9. Provide per-domain checklist/downloadable instructions if implemented, with actual detected record values and correct DNS host labels.
10. Run regression tests for old/new MX and SPF, multi-domain workspaces, custom hosts, tenant isolation, mail send/receive, IMAP/SMTP clients and PostBox.

## Rollback & legacy retirement

Maintain legacy MX and SPF include, certificate, HTTPS bridge and custom CNAME target while any tenant still depends on them. DNS TTL propagation alone is insufficient evidence of migration completion. Retire only after customer inventories and functional verification prove no remaining active dependencies.

**Phase C must NOT change production MX, mail routing, tenant mailbox data or customer DNS.**
