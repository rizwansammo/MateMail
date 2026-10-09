> **CURRENT DOCUMENTATION NOTICE (2026-10-09):** Historical custom-host pilot under the retired identity; preserved for audit. Any old DNS CNAME target, dedicated tenant deployment, or certificate procedure below is NOT an instruction for the current `matemail.pro` Fresh environment.
> Authoritative current references: [Architecture](ARCHITECTURE.md),
> [Deployment](DEPLOYMENT.md), and [Backup/Azure DR](BACKUP_RESTORE.md).
> Sections below may describe historical migration states or retired domains.

---

# NetaMate Custom-Hostname Production Migration

**Status:** COMPLETE  
**Completed:** 2026-10-06  
**Tenant:** `netamate-solutions`

## Final production state

NetaMate now uses the same database-backed custom-host lifecycle as every other
MateMail organization:

| Surface | Hostname | DNS | HTTPS | Routing |
|---|---|---|---|---|
| MateMail Hub | `mailhub.netamate.com` | CNAME → `custom.matemail.online` | Active | Active |
| PostBox | `postbox.netamate.com` | CNAME → `custom.matemail.online` | Active | Active |

Both generated nginx vhosts proxy UI traffic to the canonical MateMail frontend
(`matemail_frontend` / host loopback `127.0.0.1:3020`) and API traffic to the
shared MateMail backend.

## Legacy state retired

The migration no longer depends on the former dedicated NetaMate layer:

- `DEDICATED_TENANT_HOSTS` is empty;
- `mailadmin.netamate.com` and `postbox.netamate.com` are not fixed Django hosts;
- the temporary per-host frontend override mechanism has been removed from MateMail;
- the dedicated frontend on `127.0.0.1:3060` is stopped and removed;
- `/opt/NetaMate-Email` is removed from MateServer;
- the old NetaMate-specific frontend image is removed from the host;
- hand-written `mailadmin.netamate.com` and `postbox.netamate.com` nginx vhosts are removed;
- the obsolete `mailadmin.netamate.com` certificate was revoked and deleted.

The active PostBox and MailHub certificates remain under Certbot because the
generated custom-host vhosts use them.

## Acceptance evidence

After retirement of the dedicated frontend and fixed-host bindings:

- `https://mailhub.netamate.com/` returned HTTP 200;
- `https://postbox.netamate.com/` returned HTTP 200;
- Hub rejected PostBox/Platform routes with 404;
- PostBox rejected Workspace/Platform routes with 404;
- `nginx -t` passed;
- both custom-host rows remained `verified / active / active`.

The canonical MateMail frontend is now the only frontend deployment required
for these custom URLs.
