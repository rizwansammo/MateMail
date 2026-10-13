"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { PageHeader, Spinner } from "@/components/platform/ui";

type Proposal = {
  domain: string;
  sender_name: string;
  noreply_address: string;
  security_address: string;
  operations_address: string;
  dmarc_address: string;
  tlsrpt_address: string;
};
type Config = {
  revision: number;
  draft: (Proposal & { revision: number; created_at: string }) | null;
  defaults: Proposal;
  operational: { sender: string; dmarc_receiver: string; tlsrpt_receiver: string; source: string; draft_applied: boolean };
  activation_supported: boolean;
  mailboxes_provisioned: boolean;
  staging_request_enabled: boolean;
  dkim_preparation_request_enabled: boolean;
};
type Readiness = {
  checked_at: string;
  platform_ownership: { target: string; legacy: string };
  engine: { status: string; legacy_domain: string; target_domain: string; legacy_sender: string; target_roles: Record<string, string>; reason: string };
  runtime_alignment: { roles: Record<string, string>; legacy_configuration_intact: boolean; migration_started: boolean };
  outstanding_gates: Record<string, string>;
  cutover_ready: boolean;
};
type History = { results: Array<{ revision: number; created_at: string; state: string }> };
type DNSReadiness = {
  domain: string;
  expected_mx: string;
  mx: string;
  spf: string;
  dmarc: string;
  dkim_publication: string;
  dkim_engine_key_verified: boolean;
  inbound_delivery_verified: boolean;
  outbound_delivery_verified: boolean;
  cutover_ready: boolean;
  note: string;
};
const PREFIX = "/api/platform/system-mail";
const FIELDS: Array<{ key: keyof Proposal; title: string }> = [
  { key: "domain", title: "Reserved system domain" },
  { key: "sender_name", title: "Display name" },
  { key: "noreply_address", title: "General notifications" },
  { key: "security_address", title: "Security notifications" },
  { key: "operations_address", title: "Operations" },
  { key: "dmarc_address", title: "DMARC reports" },
  { key: "tlsrpt_address", title: "TLS reports" },
];
const formatError = (err: unknown) => {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : "The request failed.";
};

export default function SystemMailPage() {
  const [config, setConfig] = useState<Config | null>(null);
  const [form, setForm] = useState<Proposal | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [dnsReadiness, setDnsReadiness] = useState<DNSReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [staging, setStaging] = useState(false);
  const [preparingDkim, setPreparingDkim] = useState(false);
  const [preparedDkim, setPreparedDkim] = useState<{ dns_record_name: string; dns_record_value: string } | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [next, status, revisions] = await Promise.all([
        api.get<Config>(`${PREFIX}/configuration/`),
        api.get<Readiness>(`${PREFIX}/readiness/`),
        api.get<History>(`${PREFIX}/history/`),
      ]);
      setConfig(next);
      setForm(next.draft ?? next.defaults);
      setReadiness(status);
      setHistory(revisions);
      // Public DNS may time out. It must not block the existing admin configuration UI.
      void api.get<DNSReadiness>(`${PREFIX}/dns-readiness/`)
        .then(setDnsReadiness)
        .catch(() => setDnsReadiness(null));
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // Defer the initial load so state updates are not performed synchronously inside an effect.
  useEffect(() => {
    void Promise.resolve().then(refresh);
  }, [refresh]);

  async function save() {
    if (!config || !form || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.post<Config>(`${PREFIX}/configuration/`, {
        expected_revision: config.revision,
        ...form,
      });
      setConfig(next);
      setForm(next.draft ?? next.defaults);
      setNotice("Configuration draft saved. Live mail delivery and DNS are unchanged.");
      // History is secondary. A temporary read error must not report a successful save as failed.
      void api.get<History>(`${PREFIX}/history/`).then(setHistory).catch(() => {
        setNotice("Draft saved. Revision history could not be refreshed yet.");
      });
    } catch (err) {
      setError(formatError(err));
    } finally {
      setSaving(false);
    }
  }

  async function stageInactive() {
    if (!config?.draft || !config.staging_request_enabled || staging || saving) return;
    if (!window.confirm(
      "Stage five inactive, passwordless system mailbox placeholders? " +
      "No passwords, DKIM keys, or live mail routing will be activated."
    )) return;
    setStaging(true);
    setError("");
    setNotice("");
    try {
      const result = await api.post<{
        state: string; roles: number; delivery_activated: boolean; dkim_modified: boolean;
      }>(`${PREFIX}/stage/`, {
        expected_revision: config.revision,
        confirmation: "stage_inactive",
      });
      if (result.state !== "inactive" || result.roles !== 5 ||
          result.delivery_activated || result.dkim_modified) {
        setError("Staging outcome requires review. Do not retry until Native Engine state is checked.");
        return;
      }
      setNotice("Five inactive system identities staged. Sending, passwords and DKIM remain unchanged.");
      // Refresh only readiness. Keep the confirmation notice visible.
      const latest = await api.get<Readiness>(`${PREFIX}/readiness/`);
      setReadiness(latest);
    } catch (err) {
      setError("Staging could not be confirmed. Inspect the Native Engine and audit log before retrying. " + formatError(err));
    } finally {
      setStaging(false);
    }
  }

  async function prepareInactiveDkim() {
    if (!config?.draft || !config.dkim_preparation_request_enabled || preparingDkim || saving) return;
    if (readiness?.engine.target_domain !== "inactive" ||
        Object.values(readiness.engine.target_roles).some(state => state !== "inactive")) return;
    if (!window.confirm(
      "Prepare the system.matemail.pro DKIM key for DNS publication? " +
      "No mail will be activated and the current mail.matemail.pro DKIM key will remain untouched."
    )) return;
    setPreparingDkim(true);
    setPreparedDkim(null);
    setError("");
    setNotice("");
    try {
      const result = await api.post<{
        dns_record_name: string; dns_record_value: string;
        state: string; mail_delivery_activated: boolean; credentials_created: boolean;
      }>(`${PREFIX}/prepare-dkim/`, {
        expected_revision: config.revision,
        confirmation: "prepare_dkim_inactive",
      });
      if (result.state !== "inactive" || result.mail_delivery_activated ||
          result.credentials_created || !result.dns_record_name ||
          !result.dns_record_value) {
        setError("DKIM preparation result was not confirmed. Review the Native Engine before retrying.");
        return;
      }
      setPreparedDkim({
        dns_record_name: result.dns_record_name,
        dns_record_value: result.dns_record_value,
      });
      setNotice("System DKIM public record is ready. Publish the exact TXT value before any sender change.");
    } catch (err) {
      setError("DKIM preparation is unconfirmed. Inspect the engine before retrying. " + formatError(err));
    } finally {
      setPreparingDkim(false);
    }
  }

  const unchanged = Boolean(config && form && FIELDS.every(({ key }) => form[key] === (config.draft ?? config.defaults)[key]));

  return (
    <div className="space-y-5">
      <PageHeader title="System Mail" description="Platform owned mail identities and planned migration. Draft changes never change live mail routing." />
      <div className="pf-card p-4 space-y-2">
        <p className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>Production protection</p>
        <p className="text-sm pf-muted">Live sender, DNS, DKIM, existing mailboxes and system domain routing remain unchanged. Saving here records an audited draft only. Activation is not available in Release 2.</p>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="pf-badge">Activation disabled</span>
          <span className="pf-badge">No DNS changes</span>
          <span className="pf-badge">No mailbox provisioning</span>
        </div>
      </div>
      {error && <div role="alert" className="pf-card p-3 text-sm" style={{ color: "var(--pf-danger)" }}>{error} <button className="pf-btn ml-2" onClick={() => void refresh()}>Refresh</button></div>}
      {notice && <p role="status" className="text-sm" style={{ color: "var(--pf-success, #16a34a)" }}>{notice}</p>}
      {loading ? <Spinner /> : config && form ? (
        <>
          <section className="pf-card p-5 space-y-4" aria-label="System mail configuration">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>Proposed identities</h2><p className="text-xs pf-muted">Draft revision {config.revision}. Addresses are reserved by the backend.</p></div>
              <button className="pf-btn" disabled={saving} onClick={() => void refresh()}>Discard changes</button>
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {FIELDS.map(({ key, title }) => (
                <label key={key} className="block space-y-1 text-xs pf-muted">
                  <span>{title}</span>
                  <input className="pf-input w-full" value={form[key]} disabled={key !== "sender_name" || saving} maxLength={key === "sender_name" ? 80 : undefined} onChange={(event) => setForm((previous) => previous ? { ...previous, [key]: event.target.value } : previous)} />
                </label>
              ))}
            </div>
            <div className="flex items-center justify-end gap-3">
              <span className="text-xs pf-muted">This saves a proposal only.</span>
              <button className="pf-btn pf-btn-primary" disabled={saving || unchanged || !form.sender_name.trim()} onClick={() => void save()}>{saving ? "Saving…" : "Save draft"}</button>
            </div>
          </section>
          {config.staging_request_enabled && <section className="pf-card p-5 space-y-3" aria-label="Inactive system mailbox staging">
            <h2 className="font-semibold" style={{ color: "var(--pf-text)" }}>Inactive mailbox staging</h2>
            <p className="text-sm pf-muted">Prepare five disabled system identities only. This cannot activate delivery, generate mailbox passwords, switch senders or create a DKIM key. Native Engine may still refuse staging if its own independent gate is closed.</p>
            <button className="pf-btn" disabled={!config.draft || !unchanged || staging || saving} onClick={() => void stageInactive()}>
              {staging ? "Staging inactive identities…" : "Stage inactive identities"}
            </button>
            {!config.draft && <p className="text-xs pf-muted">Save a configuration draft first.</p>}
          </section>}
          {config.dkim_preparation_request_enabled && <section className="pf-card p-5 space-y-3" aria-label="Inactive system DKIM preparation">
            <h2 className="font-semibold" style={{ color: "var(--pf-text)" }}>Prepare system DKIM public record</h2>
            <p className="text-sm pf-muted">Prepare a signing key for the inactive system domain only. The current DKIM key, mail delivery and sender configuration remain unchanged.</p>
            <button className="pf-btn" disabled={!config.draft || !unchanged || preparingDkim || staging ||
              readiness?.engine.target_domain !== "inactive" ||
              Object.values(readiness?.engine.target_roles ?? {}).some(state => state !== "inactive")}
              onClick={() => void prepareInactiveDkim()}>
              {preparingDkim ? "Preparing DKIM…" : "Prepare DKIM TXT"}
            </button>
            {preparedDkim && <div className="space-y-2 text-xs">
              <p className="pf-muted">Publish this exact record in Spaceship DNS. Never paste a private key.</p>
              <p>TXT host: <code className="break-all">{preparedDkim.dns_record_name}</code></p>
              <p>TXT value:</p>
              <textarea className="pf-input w-full" value={preparedDkim.dns_record_value} readOnly rows={4} aria-label="System DKIM public TXT value" />
            </div>}
          </section>}
          <section className="pf-card p-5 space-y-3">
            <h2 className="font-semibold" style={{ color: "var(--pf-text)" }}>Current operational configuration</h2>
            <div className="grid gap-3 text-sm md:grid-cols-3">
              <div><p className="pf-muted text-xs">Notification sender</p><p className="break-all">{config.operational.sender || "Not configured"}</p></div>
              <div><p className="pf-muted text-xs">DMARC receiver</p><p className="break-all">{config.operational.dmarc_receiver || "Not configured"}</p></div>
              <div><p className="pf-muted text-xs">TLS report receiver</p><p className="break-all">{config.operational.tlsrpt_receiver || "Not configured"}</p></div>
            </div>
            <p className="text-xs pf-muted">Source: {config.operational.source}. Draft applied: {config.operational.draft_applied ? "Yes" : "No"}.</p>
          </section>
          {readiness && <section className="pf-card p-5 space-y-4">
            <div className="flex justify-between gap-2"><h2 className="font-semibold" style={{ color: "var(--pf-text)" }}>Readiness inventory</h2><span className="text-xs pf-muted">{new Date(readiness.checked_at).toLocaleString()}</span></div>
            <p className="text-sm pf-muted">This inventory is not an activation approval or proof of mail delivery.</p>
            <div className="grid gap-3 text-sm md:grid-cols-2">
              <div>Platform domain reservation: <strong>{readiness.platform_ownership.target}</strong></div>
              <div>Existing domain reservation: <strong>{readiness.platform_ownership.legacy}</strong></div>
              <div>Existing engine domain: <strong>{readiness.engine.legacy_domain}</strong></div>
              <div>Target engine domain: <strong>{readiness.engine.target_domain}</strong></div>
            </div>
            <div><h3 className="mb-2 text-sm font-semibold">Target mailbox inventory</h3><div className="grid gap-2 sm:grid-cols-2">{Object.entries(readiness.engine.target_roles).map(([name, state]) => <p key={name} className="text-sm pf-muted">{name.replaceAll("_", " ")}: <strong>{state}</strong></p>)}</div></div>
            <div><h3 className="mb-2 text-sm font-semibold">Migration checks still required</h3><div className="grid gap-2 sm:grid-cols-2">{Object.entries(readiness.outstanding_gates).map(([name, state]) => <p key={name} className="text-sm pf-muted">{name.replaceAll("_", " ")}: <strong>{state.replaceAll("_", " ")}</strong></p>)}</div></div>
          </section>}
          {dnsReadiness && <section className="pf-card p-5 space-y-3" aria-label="System domain DNS observations">
            <h2 className="font-semibold" style={{ color: "var(--pf-text)" }}>Release 3 DNS observations</h2>
            <p className="text-sm pf-muted">Read only checks for {dnsReadiness.domain}. DNS records do not approve mailbox activation or the sender cutover.</p>
            <div className="grid gap-2 text-sm sm:grid-cols-2">
              {(["mx", "spf", "dmarc", "dkim_publication"] as const).map(key =>
                <p key={key} className="pf-muted">{key.replaceAll("_", " ").toUpperCase()}: <strong>{dnsReadiness[key].replaceAll("_", " ")}</strong></p>
              )}
            </div>
            <p className="text-xs pf-muted">Expected MX: {dnsReadiness.expected_mx}. Actual Native Engine DKIM key match: not verified. Live delivery: not tested.</p>
            <p className="text-xs pf-muted">{dnsReadiness.note}</p>
          </section>}
          <section className="pf-card p-5 space-y-2">
            <h2 className="font-semibold" style={{ color: "var(--pf-text)" }}>Draft history</h2>
            {history?.results.length ? <ul className="space-y-2">{history.results.map((row) => <li className="text-sm pf-muted" key={row.revision}>Revision {row.revision} · {row.state} · {new Date(row.created_at).toLocaleString()}</li>)}</ul> : <p className="text-sm pf-muted">No configuration drafts have been saved.</p>}
          </section>
        </>
      ) : !error ? <p className="pf-muted text-sm">Configuration is unavailable.</p> : null}
    </div>
  );
}
