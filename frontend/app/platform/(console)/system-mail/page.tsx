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
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [next, status, revisions, observedDns] = await Promise.all([
        api.get<Config>(`${PREFIX}/configuration/`),
        api.get<Readiness>(`${PREFIX}/readiness/`),
        api.get<History>(`${PREFIX}/history/`),
        api.get<DNSReadiness>(`${PREFIX}/dns-readiness/`),
      ]);
      setConfig(next);
      setForm(next.draft ?? next.defaults);
      setReadiness(status);
      setHistory(revisions);
      setDnsReadiness(observedDns);
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
