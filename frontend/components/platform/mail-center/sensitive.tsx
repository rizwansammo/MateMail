"use client";

import { useCallback, useEffect, useState } from "react";
import { LockKeyhole, RefreshCw, ShieldAlert, UserRoundCheck } from "lucide-react";
import { ApiError } from "@/lib/api";
import { describeError } from "@/contexts/platform-auth-context";
import { mailCenterApi, type MailGrant, type MailRole } from "@/lib/mail-center-api";
import { Card, ErrorNote, Spinner, SuccessNote } from "@/components/platform/ui";
import { MailReader } from "./operations";

type GrantRow = {
  grant_id: string;
  role: "security" | "noreply";
  grantee_id: string;
  grantee_email: string;
  expires_at: string;
};

export function MailSensitiveAccess() {
  const [grants, setGrants] = useState<MailGrant[]>([]);
  const [inventory, setInventory] = useState<GrantRow[]>([]);
  const [inventoryAllowed, setInventoryAllowed] = useState(false);
  const [role, setRole] = useState<MailRole | null>(null);
  const [userId, setUserId] = useState("");
  const [grantRole, setGrantRole] = useState<"security" | "noreply">("security");
  const [duration, setDuration] = useState(24);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [requiresVerification, setRequiresVerification] = useState(false);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    Promise.allSettled([mailCenterApi.grants(), mailCenterApi.grantInventory()]).then(([mine, all]) => {
      if (!active) return;
      if (mine.status === "fulfilled") {
        setGrants(mine.value.permissions);
        setRequiresVerification(false);
        setError("");
      } else {
        setGrants([]);
        const error = mine.reason;
        setRequiresVerification(error instanceof ApiError && error.status === 403);
        setError(describeError(error, "Unable to load your sensitive mailbox permissions."));
      }
      if (all.status === "fulfilled") {
        setInventory(all.value.results);
        setInventoryAllowed(true);
      } else {
        setInventory([]);
        setInventoryAllowed(false);
      }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tick]);

  async function grant() {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await mailCenterApi.grant(userId, grantRole, duration);
      setNotice("Temporary read grant approved and audited.");
      setUserId("");
      refresh();
    } catch (caught) {
      setError(describeError(caught, "Could not approve the requested grant."));
    } finally { setBusy(false); }
  }

  async function revoke(item: GrantRow) {
    if (busy || !window.confirm("Revoke " + item.grantee_email + " access to " + item.role + "?")) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await mailCenterApi.revoke(item.grant_id);
      setNotice("Grant revoked. Its existing session can no longer read this mailbox.");
      refresh();
    } catch (caught) {
      setError(describeError(caught, "The grant could not be revoked."));
    } finally { setBusy(false); }
  }

  return (
    <section className="space-y-4" aria-label="Sensitive system mailbox permissions">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold" style={{ color: "var(--pf-text)" }}>Security &amp; Notification Access</h2>
          <p className="mt-1 text-xs pf-muted">
            Sensitive mailboxes require recent Platform Console email verification and temporary approval.
          </p>
        </div>
        <button type="button" className="pf-btn pf-btn-ghost" onClick={refresh}>
          <RefreshCw className="h-4 w-4" /> Refresh permissions
        </button>
      </div>
      {error && <ErrorNote message={error} />}
      {notice && <SuccessNote message={notice} />}
      {requiresVerification && (
        <div className="flex items-start gap-3 rounded-lg p-3 text-sm"
          style={{ background: "var(--pf-warn-soft)", color: "var(--pf-warn)" }}>
          <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
          <span>This sensitive session needs a fresh email OTP verification. Sign out and sign back in to the Platform Console.</span>
        </div>
      )}
      {loading ? <Spinner label="Checking temporary access grants" /> : (
        <>
          <Card>
            <h3 className="text-sm font-semibold">Your active read grants</h3>
            {!grants.length ? (
              <p className="mt-3 text-sm pf-muted">No active grants. Sensitive mailbox messages are inaccessible.</p>
            ) : (
              <div className="mt-3 flex flex-wrap gap-2">
                {grants.map((item) => (
                  <button type="button" key={item.role} className="pf-btn pf-btn-ghost"
                    aria-pressed={role === item.role}
                    onClick={() => setRole(item.role)}>
                    <ShieldAlert className="h-4 w-4" />
                    {item.role === "security" ? "Security" : "Noreply"}
                    <span className="text-xs pf-muted">
                      expires {new Date(item.expires_at).toLocaleString()}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </Card>
          {role && grants.some((item) => item.role === role) &&
            <MailReader key={role} role={role} sending={false} />}

          {inventoryAllowed && (
            <Card>
              <div className="mb-2 flex items-center gap-2">
                <UserRoundCheck className="h-4 w-4" />
                <h3 className="text-sm font-semibold">Superuser grant management</h3>
              </div>
              <p className="text-xs pf-muted">
                Approval requires a second active Platform Administrator. Self approval and permanent grants are not allowed.
              </p>
              <div className="mt-3 grid gap-3 md:grid-cols-3">
                <label className="text-xs pf-muted">
                  Grantee ID (UUID)
                  <input className="pf-input mt-1" value={userId}
                    onChange={(e) => setUserId(e.target.value)} placeholder="Platform administrator ID" />
                </label>
                <label className="text-xs pf-muted">
                  Mailbox role
                  <select className="pf-input mt-1" value={grantRole}
                    onChange={(e) => setGrantRole(e.target.value as "security" | "noreply")}>
                    <option value="security">Security</option>
                    <option value="noreply">Noreply</option>
                  </select>
                </label>
                <label className="text-xs pf-muted">
                  Grant duration (hours, max 168)
                  <input className="pf-input mt-1" type="number" min={1} max={168}
                    value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
                </label>
              </div>
              <button type="button" className="pf-btn pf-btn-primary mt-3" disabled={busy || !userId.trim()}
                onClick={() => void grant()}>Approve temporary read grant</button>
              <h4 className="mt-5 text-sm font-semibold">Currently approved grants</h4>
              {inventory.length === 0 ? <p className="mt-2 text-xs pf-muted">No active sensitive mailbox grants.</p> : (
                <div className="mt-2 space-y-2">
                  {inventory.map((item) => (
                    <div key={item.grant_id} className="flex flex-wrap items-center justify-between gap-3 border-t py-2"
                      style={{ borderColor: "var(--pf-border)" }}>
                      <div className="min-w-0 text-xs">
                        <p className="break-all font-medium">{item.grantee_email}</p>
                        <p className="pf-muted">{item.role} · expires {new Date(item.expires_at).toLocaleString()}</p>
                      </div>
                      <button type="button" className="pf-btn pf-btn-danger" disabled={busy}
                        onClick={() => void revoke(item)}>Revoke</button>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </>
      )}
    </section>
  );
}
