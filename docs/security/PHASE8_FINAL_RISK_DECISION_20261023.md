# Phase 8 Final Security Risk Decision and Bounded Exceptions

**Date:** 2026-10-23 (UTC)  
**Release verified in production:** `924a78ea1f1f6f418df163dd7bcf9112c262a243`  
**Subject:** MateMail Native Engine vulnerability reachability, preventive controls, temporary residual-risk disposition  
**Scope:** Risk assessment and Phase 8 milestone closeout; **NOT a claim that installed vulnerable libraries are patched**, that all message sizes are scanned, or that the service has zero security risks.

## Executive risk decision

After read-only live runtime verification and vendor advisory review:

1. **Dovecot MariaDB CVEs:** Classify the four package-matched MariaDB advisories as **not reachable through the currently configured PostgreSQL authentication/database workflow**. These are not four known remotely exploitable Dovecot service flaws. The MariaDB client library still exists and was previously seen mapped by a Dovecot process, so this is a **configuration-dependent not-applicable assessment**, not a claim of a package patch or absence of all possible reachability.
2. **Rspamd libxml2 CVE-2026-6653:** Classify as **residual Moderate availability risk**, not a demonstrated mailbox confidentiality compromise. The installed 4.2.1 and the fully scanned official 4.2.3 candidate both retain this affected library. Debian Trixie marks it vulnerable with `no-dsa` (minor issue), while the published impact is highly conditional, malicious XML-driven denial of service. A message-to-`xmlParseInternalSubset` exploitation chain in MateMail was **not proven or excluded**.
3. **Decision:** **Timeboxed acceptance of the two above residual CVE risk groups through 2026-11-22** for Phase 8 *milestone* closure **with documented exceptions**, conditional on the verified operational controls below remaining in place. This is the documented operational decision requested by the project owner; it is not a claim of a separate electronic signature or permanent authorization.
4. **No approval to disable checks or ship an image failing a security gate.** Maintain Draft PR #156 without merging. Issue #76 stays **OPEN** as the tracked remediation and fixed-image acceptance workstream.

## Advisory-specific applicability

| CVE | Affected mechanism | Verified MateMail scope | Decision |
|---|---|---|---|
| CVE-2026-44172 | MariaDB Connector/C `mysql_real_escape_string` and BIG5 SQL text protocol | Dovecot effective auth configuration identifies PostgreSQL; no configured MySQL/MariaDB auth | No currently configured vulnerable connector operation shown; conditional Not Applicable |
| CVE-2026-49261 | MariaDB Server/Galera `wsrep_notify_cmd` | Native database is PostgreSQL, with no MariaDB server or Galera cluster in the engine stack | Not Applicable to deployed architecture |
| CVE-2026-85746 | MariaDB Server `ST_AsText` recursive GIS serialization | No MariaDB server or GIS SQL execution path in the deployed native engine | Not Applicable to deployed architecture |
| CVE-2026-92262 | MariaDB Server `#mysql50#` legacy table filesystem encoding | No MariaDB server/database table operations in the deployed native engine | Not Applicable to deployed architecture |
| CVE-2026-6653 | libxml2 `xmlParseInternalSubset` use-after-free via malicious XML; availability impact | Rspamd binary maps `libxml2.so.2`; scans untrusted mail via internal Milter; call-path to the vulnerable function is not verified | **Open vulnerability; temporarily accepted Moderate availability risk**, not patched |

The old Trivy Dovecot count of **8 Critical entries** is four distinct MariaDB advisories reported against two package records (`libmariadb3`, `mariadb-common`). The Rspamd count is **1 Critical advisory entry**; its vulnerability remains real at the package level.

## Read-only production control verification (2026-10-23)

- Dovecot: actual `doveconf -n` command returned success, showed PostgreSQL configuration and **no MySQL/MariaDB configuration**. The MariaDB client shared object remains present in the shipped image.
- Native Database: `postgres:16-alpine` is running and healthy; Dovecot and Postfix health checks are passing.
- Rspamd: production image `rspamd/rspamd:4.2.1`, healthy, zero recorded restarts and not OOM-killed at assessment time.
- Rspamd directly publishes **zero host ports** and operates behind Postfix on the internal Native Engine network.
- Rspamd Docker memory limit **700 MiB**, container restart policy `unless-stopped`, `task_timeout=30s` and `soft_reject_on_timeout=true`.
- Postfix effective `smtpd_milters` and `non_smtpd_milters` configured; `milter_default_action=tempfail`. A scanning outage can delay SMTP deliveries rather than transparently failing open *on the verified Milter failure path*. This does not by itself prove every mail-flow branch or oversize message is scanned.
- Rspamd `rspamadm configtest` passed.
- Prometheus time series at review: `matemail_native_service_up{service="rspamd"}=1`, `matemail_native_service_healthy{service="rspamd"}=1`, `matemail_native_service_restarts_total{service="rspamd"}=0`, collector age 49 seconds.
- Prometheus loaded the **NativeServiceDown**, **NativeServiceUnhealthy**, **NativeServiceRestartLoop** and **NativeQueueGrowing** alert rules. No such service alerts were firing at inspection time. These are active monitor and alert definitions, but successful human notification delivery was not re-tested.

**Limits:** Memory capping and `tempfail` reduce blast radius; they neither prevent malicious XML from causing a scanner crash nor ensure unchanged deliverability during a sustained attack. No malicious exploitation test was attempted against production.

## Do not silently overlook the other High findings

The previous exact-image scoped OS scan also recorded **67 High advisory rows in Dovecot (26 distinct IDs)** and **50 High rows in Rspamd (15 distinct IDs)**. The Dovecot scan flagged **11 High rows with an identified fix**, concentrated in packages such as `libexpat1` and `curl`/`libcurl4t64`. These are **not certified remediated by this risk decision**.

**Follow-up deadline for available High patches: 2026-11-06**. Inspect fixed upstream/base image candidates in CI, preserve image digest, isolate package updates from production mail, scan again, and verify auth, delivery, MIME, DKIM and filtering paths before manual deployment. Remaining nonfixed High advisories stay in issue #76 for prioritization.

## Message size configuration review (separate follow-up)

At inspection time, Postfix reported `message_size_limit=104857600` (100 MiB), while MateMail's Rspamd local `options.inc` did not explicitly override `max_message`; Rspamd documents the default as **50 MiB**. This is a potential scan-size policy mismatch and requires validation before claiming that all accepted messages are scanned. It is **not proven** here that oversized messages bypass filtering: the Milter error/overlimit behavior was not tested. Investigate and align limits without silently reducing customer attachment limits or increasing memory pressure. Follow-up target: **2026-10-30**.

## Remediation trigger and expiry

**Owner:** MateMail platform operator, with remediation tracked publicly through GitHub issue #76 but without private infrastructure evidence or credentials.  
**Risk acceptance expires:** **2026-11-22** unless reevaluated with new evidence.

Immediately reopen active incident response and reconsider the exception if any of these occurs:

- Rspamd unexpectedly crashes, is unhealthy, is OOM-killed, or restarts repeatedly; collector/rule coverage stops; the mail queue and deferrals increase.
- The Postfix `tempfail`, Rspamd `soft_reject_on_timeout`, isolation, or memory limit is removed.
- A reachable Rspamd/libxml2 exploit is demonstrated, a vendor patch is available, a new advisory raises impact, or an unreviewed upgrade enables MariaDB authentication.
- A true secret exposure, data leak, mailbox compromise or other new blocking exploit evidence is discovered.

Follow-up steps are **not optional patches to bypass**: pursue a vendor-compatible Rspamd libxml2 fix and an updated Dovecot base, verify exact candidate image and mail-flow compatibility, and perform manual digest-pinned production rollout after acceptance. **Draft PR #156 intentionally failed its CVE gate** on Rspamd 4.2.3 and must remain unmerged.

## Evidence and references

- Protected exact-running-image Trivy OS JSON reports: `/root/matemail-phase8-evidence/scan-20261023/`. Neither scan data nor secrets are required in the public repository.
- GitHub [Issue #76](https://github.com/rizwansammo/MateMail/issues/76), [Draft PR #156](https://github.com/rizwansammo/MateMail/pull/156), [failed-closed Rspamd CVE acceptance workflow](https://github.com/rizwansammo/MateMail/actions/runs/39935723546).
- Debian security advisory for CVE-2026-6653: https://security-tracker.debian.org/tracker/CVE-2026-6653
- Vendor Rspamd configuration defaults: https://docs.rspamd.com/guides/configuration/fundamentals/
- Vendor CVEs: https://www.cve.org/CVERecord?id=CVE-2026-44172 ; https://nvd.nist.gov/vuln/detail/CVE-2026-49261 ; https://www.cve.org/CVERecord?id=CVE-2026-85746 ; https://www.cve.org/CVERecord?id=CVE-2026-92262

## Authorization statement

The project owner directed the assistant on 2026-10-23 to determine actual risk, select necessary mitigation, decide which residual risk is acceptable, and continue to Phase 8 closeout. The decision above is recorded **within that scope**, explicitly as **timeboxed and conditional**. It is not a separate signed risk-acceptance document, a permanent waiver, or a statement that Critical/High package advisories no longer exist. A further owner sign-off is required if NetaMate's governance process requires a signed exception.
