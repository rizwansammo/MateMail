# Phase E2 — Native Mail Identity Cutover (runbook)

## Target / rollback boundary

- Target SMTP hostname and HELO/EHLO: `mx.matemail.pro`
- Target transactional identity: `noreply@mail.matemail.pro`
- DKIM: `d=mail.matemail.pro`, selector `mm1`, unique 2048-bit private key generated and retained inside Native Engine.
- Legacy authenticated sender `noreply@mail.matemail.online` must remain active until new DNS, SMTP authentication and external delivery tests succeed.
- Reuse the current root-only Phase E1 recovery snapshot and verify fresh mail queue status before any cutover.
- Do **not** assume a hostname change isolates sender IP reputation; outbound IPv4 remains `169.58.114.252`.

## Required authoritative DNS before cutover

Zone: `matemail.pro` (Spaceship).

| Type | Host | Value / target | Why |
| --- | --- | --- | --- |
| MX | `mail` | priority `10`, `mx.matemail.pro` | Receive sender-domain bounces and replies |
| TXT | `mail` | `v=spf1 include:_spf.matemail.pro -all` | Authorize sending IP by reference |
| TXT | `mm1._domainkey.mail` | **Use actual new Native Engine DKIM public value** | Authenticate messages signed by new sender domain |
| TXT | `_dmarc.mail` | `v=DMARC1; p=none; adkim=s; aspf=s` | Monitor strict alignment first |

To retrieve the **public** DKIM value (never the private key):
```sh
docker exec matemail-backend-1 python manage.py shell -c 'from apps.mail_engine.factory import get_adapter; x=get_adapter().get_dkim_public_key("mail.matemail.pro"); print(x.dns_record_name); print(x.dns_record_value)'
```
If the DNS editor limits a TXT character-string to 255 bytes, add multiple quoted character-strings within **one** TXT record for the long DKIM key; do not create two TXT records with competing `v=DKIM1` values.

Before PTR change verify authoritative DNS across more than one resolver. The `mx.matemail.pro` A record must resolve to `169.58.114.252`.

## E2 deployment sequence (do not skip gates)

1. Verify DNS: MX sender domain, exactly one SPF policy, correct DKIM `p=` matches Native Engine, DMARC `p=none`, and forward A for `mx.matemail.pro`.
2. Verify the newly provisioned `mail.matemail.pro` domain and `noreply@mail.matemail.pro` mailbox are ACTIVE in the Native Engine, and `selectors.map` contains `mail.matemail.pro mm1`.
3. Add `mx.matemail.pro` internal alias to the submission gateway **while preserving the old alias**. Test backend TLS to Postfix and Dovecot and SMTP authentication on new sender before changing `EMAIL_HOST`.
4. Change Postfix `myhostname` to `mx.matemail.pro` and `mydomain` to `matemail.pro`; confirm public SMTP banner/EHLO. Coordinate VPS provider PTR `169.58.114.252 -> mx.matemail.pro` at the same cutover; if provider update cannot be completed, do not claim final identity alignment.
5. After SMTP identity and DNS checks, update `.env` atomically with:
   - `MAIL_DOMAIN=matemail.pro`
   - `MAIL_HOSTNAME=mx.matemail.pro`
   - `EMAIL_HOST=mx.matemail.pro`
   - `EMAIL_HOST_USER=noreply@mail.matemail.pro`
   - `DEFAULT_FROM_EMAIL=MateMail <noreply@mail.matemail.pro>`
   - `PLATFORM_SENDER_ADDRESSES=noreply@mail.matemail.pro`
   - `POSTBOX_IMAP_HOST=mx.matemail.pro`
   - `POSTBOX_SIEVE_HOST=mx.matemail.pro`
   - `POSTBOX_SIEVE_TLS_SERVER_NAME=mx.matemail.pro`
   Keep the existing `EMAIL_HOST_PASSWORD` unchanged, since the new mailbox was provisioned with that credential.
6. Deploy pinned tested images and native config in a controlled window; no Docker volume or database destruction. Check SMTP AUTH, PostBox IMAP and Sieve, platform verification/password reset send, inbound/bounce receipt, Gmail/Microsoft 365/Zoho DKIM/SPF/DMARC headers and Postfix queue.
7. Only after success: mark E2 complete. Leave old MX/CNAME and legacy certificate compatibility in place for E3/E5; no early deletion of `matemail.online`.

## Rollback

Restore Postfix main.cf, native Compose gateway alias, app Compose/.env from Phase E1 backup, and relaunch only changed services. Confirm old sender still authenticates; verify `mx.matemail.online` TLS, queue, user login and DKIM map. Do not overwrite databases or DKIM volume to roll back configuration-only changes.

## Current staging
The new mail.matemail.pro domain and noreply sender mailbox were created alongside the legacy sender in the engine. This branch carries a **future cutover configuration**; do not auto-deploy it before the required DNS and PTR coordination.
