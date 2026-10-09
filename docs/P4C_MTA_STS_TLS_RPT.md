# P4-C — MTA-STS & SMTP TLS-RPT rollout (2026-10-09)

## What is and is not complete

The first-party receiving domains are **mail.matemail.pro**, **netamate.com**, and **matedesk.pro**. All three currently point MX to **mx.matemail.pro**. The apex **matemail.pro** has no inbound MX by design; do not publish a false MTA-STS inbound policy there. When additional customer domains join, their owner must explicitly opt in and verify actual MX and certificates.

The repository contains a restricted MTA-STS **mode: testing** HTTPS policy and a DNS-independent installer. Publishing _mta-sts DNS TXT is a **separate, manual gate**, AFTER the exact HTTPS URL publicly answers with a trusted certificate, text/plain and the correct MX. Never publish \`mode: enforce\` automatically.

## Production preparation (MateServer host-native Nginx)

The script is run from its versioned checked-out directory on MateServer, not from an untrusted URL.

\`\`\`bash
sudo bash deploy/mta-sts/install.sh prepare matedesk.pro
sudo bash deploy/mta-sts/install.sh activate matedesk.pro
sudo bash deploy/mta-sts/install.sh prepare mail.matemail.pro
sudo bash deploy/mta-sts/install.sh prepare netamate.com
\`\`\`

- Existing \`mta-sts.matedesk.pro\` A points to MateServer and the current Certbot wildcard covers it. The script verifies both before opening HTTPS.
- \`mta-sts.mail.matemail.pro\` and \`mta-sts.netamate.com\` need **A=169.58.114.252** first, DNS-only/unproxied; a fresh Certbot webroot certificate is issued only after A exists and HTTP-01 bootstrap is active. After DNS has propagated, run \`activate\` for each.
- All three serve exactly \`/.well-known/mta-sts.txt\`; all other HTTPS application paths return 404. Hosted policy is in a separate \`/var/www/matemail/mta-sts\` webroot. No proxy, redirect, mail-engine modification, or customer app restart.
- Existing \`/etc/nginx/sites-available/matemail-mta-sts-<domain>\` managed configs get backed up and are automatically rolled back if verification/reload fails. The script refuses to overwrite unmanaged configs and checks \`nginx -t\` before reload.
- Do not edit Certbot renewal settings, DKIM keys, Port 25, Postfix, Dovecot, DNS MX, or Caddy.

## DNS records — publish ONLY after HTTPS policy + TLS-RPT receiving mailbox verified

All values are exactly as follows. Keep existing SPF/DKIM/DMARC TXT records. Do **not** add duplicate TXT records; when replacing a same-named record, edit the existing one.

| DNS zone | Host/Name | Type | Value | Dependency |
| --- | --- | --- | --- | --- |
| **matemail.pro** | \`mta-sts.mail\` | A | \`169.58.114.252\` | Needed before Certbot |
| **matemail.pro** | \`_mta-sts.mail\` | TXT | \`v=STSv1; id=20261009t1\` | After HTTPS |
| **matemail.pro** | \`_smtp._tls.mail\` | TXT | \`v=TLSRPTv1; rua=mailto:tlsrpt@mail.matemail.pro\` | After verified TLS-RPT receiver |
| **netamate.com** | \`mta-sts\` | A | \`169.58.114.252\` | Needed before Certbot |
| **netamate.com** | \`_mta-sts\` | TXT | \`v=STSv1; id=20261009t1\` | After HTTPS |
| **netamate.com** | \`_smtp._tls\` | TXT | \`v=TLSRPTv1; rua=mailto:tlsrpt@mail.matemail.pro\` | After verified TLS-RPT receiver |
| **matedesk.pro** | \`mta-sts\` | A | ALREADY points to \`169.58.114.252\` | Leave unchanged |
| **matedesk.pro** | \`_mta-sts\` | TXT | \`v=STSv1; id=20261009t1\` | After HTTPS |
| **matedesk.pro** | \`_smtp._tls\` | TXT | \`v=TLSRPTv1; rua=mailto:tlsrpt@mail.matemail.pro\` | After verified TLS-RPT receiver |

**DNS authority matters:** Matemail presently delegates nameservers to Spaceship; the operator must determine which UI controls that **active** zone. A registrar panel that is not serving the authoritative NS will not affect public DNS. For every record verify **authoritative** and **two independent public resolvers** before calling it done.

TLS-RPT (RFC 8460) uses \`_smtp._tls\` and mailto/HTTPS RUA; this is a *different format* from DMARC XML. Never point reports to a silently discarded address and never feed them into the DMARC XML parser. Ensure native TLS-RPT mailbox exists and report inspection/collection is operational before publishing the TXT. Do not blindly infer DMARC-style external authorization for TLS-RPT.

## Verification & safe state

\`\`\`bash
for d in mail.matemail.pro netamate.com matedesk.pro; do
  echo "Domain $d"
  dig +short MX "$d"
  dig +short TXT "_mta-sts.$d"
  dig +short TXT "_smtp._tls.$d"
  curl -fS --max-time 12 "https://mta-sts.$d/.well-known/mta-sts.txt"
done
\`\`\`

Check exactly one STS and TLS-RPT TXT per opted-in domain, cert hostname/chain, \`HTTP 200\`, \`Content-Type: text/plain\`, CRLF lines, \`mode: testing\`, \`mx: mx.matemail.pro\`, Nginx renewal readiness, SMTP STARTTLS at \`mx.matemail.pro:25\`, and unchanged Postbox/Hub/app and Postfix mail queue.

Promotion to \`mode: enforce\` is a **separate future decision** requiring several days of real report trend and backup MX / 3rd-party sender inventory. Testing-mode rollout itself is complete when its HTTPS policies, TXT records, report receiver and production verification all pass; **no waiting for an arbitrary first report** as a release gate.
