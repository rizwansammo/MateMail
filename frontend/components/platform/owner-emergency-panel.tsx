"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, KeyRound, ShieldCheck } from "lucide-react";
import {
  platformApi, type OwnerEmergencyCase, ApiError,
} from "@/lib/platform-api";
import { describeError } from "@/contexts/platform-auth-context";

type Action = "approve" | "dispatch" | "cancel";

export function OwnerEmergencyPanel({ tenantId, ownerEmail, mfaEnabled }: {
  tenantId: string; ownerEmail: string; mfaEnabled: boolean;
}) {
  const [cases, setCases] = useState<OwnerEmergencyCase[]>([]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"mfa" | "full">("mfa");
  const [reason, setReason] = useState("");
  const [evidenceReference, setEvidenceReference] = useState("");
  const [verificationNote, setVerificationNote] = useState("");
  const [password, setPassword] = useState("");
  const [selected, setSelected] = useState<{id: string; action: Action} | null>(null);
  const [reviewNote, setReviewNote] = useState("");

  const reload = useCallback(async () => {
    try {
      const data = await platformApi.ownerEmergencyCases(tenantId);
      setCases(data.cases);
      setAvailable(true);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 503) {
        setAvailable(false);
      } else {
        setError(describeError(caught, "Could not load emergency cases."));
      }
    }
  }, [tenantId]);

  useEffect(() => { void reload(); }, [reload]);

  async function start(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !mfaEnabled) return;
    setBusy(true);
    setError(""); setMessage("");
    try {
      const result = await platformApi.ownerEmergencyStart(tenantId, {
        mode, reason: reason.trim(), evidence_reference: evidenceReference.trim(),
        verification_note: verificationNote.trim(), current_password: password,
      });
      setMessage(result.detail);
      setReason(""); setEvidenceReference(""); setVerificationNote("");
      await reload();
    } catch (caught) {
      setError(describeError(caught, "Emergency case request was refused."));
    } finally {
      setPassword(""); setBusy(false);
    }
  }

  async function perform(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await platformApi.ownerEmergencyAction(
        tenantId, selected.id, selected.action,
        { current_password: password, ...(selected.action === "approve" ? { verification_note: reviewNote.trim() } : {}) },
      );
      setMessage(result.detail);
      setSelected(null); setReviewNote("");
      await reload();
    } catch (caught) {
      setError(describeError(caught, "Emergency case action was refused."));
    } finally {
      setPassword(""); setBusy(false);
    }
  }

  if (available === false) return (
    <p className="mt-3 text-xs pf-muted">
      Emergency Owner Recovery is not activated. Existing password recovery remains available.
    </p>
  );

  return (
    <section className="mt-5 border-t pt-4" style={{ borderColor: "var(--pf-border)" }} aria-labelledby="pf-owner-emergency-title">
      <div className="mb-3 flex items-start gap-2">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div>
          <h3 id="pf-owner-emergency-title" className="text-sm font-semibold">
            Emergency owner account recovery
          </h3>
          <p className="mt-1 text-xs pf-muted">
            This is for {ownerEmail} only. Two different verified Platform operators,
            a 24-hour security hold and approval from a previously verified independent
            owner recovery email are required. Operators never receive recovery credentials.
          </p>
        </div>
      </div>
      {message && <p role="status" className="mb-3 text-xs text-emerald-700 dark:text-emerald-300">{message}</p>}
      {error && <p role="alert" className="mb-3 text-xs text-red-700 dark:text-red-300">{error}</p>}
      {available === null ? <p className="text-xs pf-muted">Checking emergency recovery availability…</p> : (
        <>
          {cases.length > 0 && (
            <div className="mb-4 space-y-2">
              {cases.map((c) => (
                <div key={c.id} className="rounded-xl border p-3 text-xs" style={{ borderColor: "var(--pf-border)" }}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <strong>{c.mode === "full" ? "Full account recovery" : "Authenticator reset"}</strong>
                    <span className="pf-muted">{c.status.replace(/_/g, " ")}</span>
                  </div>
                  <p className="mt-1 pf-muted">Request: {c.initiated_by} · Review: {c.approved_by ?? "Pending"}</p>
                  <p className="mt-1 pf-muted">Case {c.id} · Evidence {c.evidence_reference}</p>
                  {c.hold_until && <p className="mt-1 pf-muted">
                    Security hold until {new Date(c.hold_until).toLocaleString()}
                  </p>}
                  {c.capability_expires_at && <p className="mt-1 pf-muted">
                    Owner approval deadline {new Date(c.capability_expires_at).toLocaleString()}
                  </p>}
                  {["pending_second_approval", "security_hold", "awaiting_owner"].includes(c.status) && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {c.status === "pending_second_approval" && (
                        <button type="button" className="pf-btn pf-btn-ghost" onClick={() => { setSelected({id:c.id,action:"approve"}); setError(""); }}>
                          Independent approval
                        </button>
                      )}
                      {c.status === "security_hold" && (
                        <button type="button" className="pf-btn pf-btn-ghost"
                          disabled={!c.hold_until || Date.parse(c.hold_until)>Date.now()}
                          onClick={() => { setSelected({id:c.id,action:"dispatch"}); setError(""); }}>
                          Send owner confirmation
                        </button>
                      )}
                      <button type="button" className="pf-btn pf-btn-danger" onClick={() => { setSelected({id:c.id,action:"cancel"}); setError(""); }}>
                        Cancel case
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {selected ? (
            <form onSubmit={perform} className="rounded-xl border p-3 space-y-3" style={{ borderColor:"var(--pf-border)" }}>
              <p className="flex items-center gap-2 text-sm font-semibold">
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                {selected.action === "approve" ? "Independent security review" :
                  selected.action === "dispatch" ? "Dispatch owner confirmation" : "Cancel emergency case"}
              </p>
              {selected.action === "approve" && (
                <label className="block text-xs font-medium">
                  Independently verified identity and ownership evidence
                  <textarea className="pf-input mt-1 w-full" minLength={20} maxLength={500} rows={3}
                    required value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} />
                </label>
              )}
              <label className="block text-xs font-medium">
                Your current Platform Console password
                <input type="password" autoComplete="current-password"
                  className="pf-input mt-1 w-full" required value={password}
                  onChange={(e) => setPassword(e.target.value)} />
              </label>
              <div className="flex flex-wrap gap-2">
                <button className="pf-btn pf-btn-primary" disabled={busy || !password ||
                  (selected.action === "approve" && reviewNote.trim().length<20)}>
                  {busy ? "Processing…" : "Confirm security action"}
                </button>
                <button className="pf-btn pf-btn-ghost" type="button" disabled={busy}
                  onClick={() => { setSelected(null); setPassword(""); setError(""); }}>
                  Back
                </button>
              </div>
            </form>
          ) : mfaEnabled ? (
            <form onSubmit={start} className="space-y-3">
              <h4 className="text-xs font-semibold">Open a new emergency case</h4>
              <label className="block text-xs font-medium">
                Recovery type
                <select className="pf-select mt-1" value={mode}
                  onChange={(e) => setMode(e.target.value as "mfa" | "full")}>
                  <option value="mfa">Reset two-factor authentication</option>
                  <option value="full">Full recovery: reset password and two-factor authentication</option>
                </select>
              </label>
              <label className="block text-xs font-medium">
                Incident reason
                <textarea className="pf-input mt-1 w-full" minLength={20} maxLength={500}
                  rows={2} required value={reason} onChange={(e) => setReason(e.target.value)} />
              </label>
              <label className="block text-xs font-medium">
                Verified identity / company ownership evidence reference
                <input className="pf-input mt-1 w-full" minLength={8} maxLength={200}
                  required value={evidenceReference} onChange={(e) => setEvidenceReference(e.target.value)} />
              </label>
              <label className="block text-xs font-medium">
                Independent verification notes
                <textarea className="pf-input mt-1 w-full" minLength={20} maxLength={500}
                  rows={2} required value={verificationNote} onChange={(e) => setVerificationNote(e.target.value)} />
              </label>
              <label className="block text-xs font-medium">
                Your current Platform Console password
                <input type="password" autoComplete="current-password"
                  className="pf-input mt-1 w-full" required value={password}
                  onChange={(e) => setPassword(e.target.value)} />
              </label>
              <button className="pf-btn pf-btn-primary" disabled={busy || !password ||
                reason.trim().length<20 || verificationNote.trim().length<20 || evidenceReference.trim().length<8}>
                <KeyRound className="h-4 w-4" aria-hidden="true" />
                {busy ? "Opening case…" : "Open emergency recovery case"}
              </button>
            </form>
          ) : <p className="text-xs pf-muted">Owner has no active authenticator. Use standard password recovery or a separately approved offline identity process.</p>}
        </>
      )}
    </section>
  );
}
