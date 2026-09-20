"use client";

/**
 * Organization detail — the consolidated operational view.
 *
 * Every control on this page maps to a backend endpoint that exists and does
 * what the button says. Where the backend has no capability, there is no
 * button: there is no "delete organization", no "log in as owner", and nothing
 * that reads the customer's mail.
 */
import { useCallback, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  Ban,
  CheckCircle2,
  KeyRound,
  Play,
  Send,
  ShieldOff,
  XCircle,
} from "lucide-react";

import { platformApi, type PlatformOwner } from "@/lib/platform-api";
import { describeError } from "@/contexts/platform-auth-context";
import {
  Badge,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorNote,
  PageHeader,
  Spinner,
  SuccessNote,
  formatDateTime,
  formatRelative,
  humanise,
  useListData,
} from "@/components/platform/ui";

interface TenantDetail {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string | null;
  owner_email: string;
  owner_id: string;
  created_at: string;
  updated_at: string;
  domain_count: number;
  mailbox_count: number;
  member_count: number;
  approved_at: string | null;
  approved_by: string | null;
  review_reason: string;
  outbound_disabled: boolean;
  outbound_disabled_at: string | null;
  can_send_outbound: boolean;
  subscription: {
    plan?: { tier?: string; display_name?: string };
    status?: string;
    trial_ends_at?: string | null;
  } | null;
  domains: Array<{ id: string; name: string; status: string; created_at: string }>;
  members: Array<{
    id: string;
    email: string;
    full_name: string;
    role: string;
    joined_at: string;
  }>;
  recent_logs: Array<{
    event_type: string;
    result: string;
    source: string;
    ip_address: string | null;
    created_at: string;
  }>;
  available_plans: Array<{ tier: string; display_name: string }>;
}

type Action =
  | { kind: "approve" }
  | { kind: "reject" }
  | { kind: "suspend" }
  | { kind: "activate" }
  | { kind: "outbound"; enable: boolean }
  | { kind: "recovery" }
  | { kind: "revoke" }
  | null;

export default function OrganizationDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const tenant = useListData(
    () => platformApi.tenant(id) as unknown as Promise<TenantDetail>,
    [id],
  );
  const owner = useListData(() => platformApi.owner(id), [id]);

  const [action, setAction] = useState<Action>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [planTier, setPlanTier] = useState("");
  const [trialDays, setTrialDays] = useState("30");
  const [planBusy, setPlanBusy] = useState(false);

  const refreshAll = useCallback(() => {
    tenant.reload();
    owner.reload();
  }, [tenant, owner]);

  const run = useCallback(
    async (reason: string) => {
      if (!action) return;
      setBusy(true);
      setActionError(null);
      try {
        switch (action.kind) {
          case "approve":
            await platformApi.approveTenant(id);
            setNotice("Organization approved.");
            break;
          case "reject":
            await platformApi.rejectTenant(id, reason);
            setNotice("Organization rejected.");
            break;
          case "suspend":
            await platformApi.suspendTenant(id, reason);
            setNotice("Organization suspended.");
            break;
          case "activate":
            await platformApi.activateTenant(id);
            setNotice("Organization reactivated.");
            break;
          case "outbound": {
            await platformApi.setOutbound(id, action.enable, reason);
            setNotice(
              action.enable ? "Outbound sending enabled." : "Outbound sending disabled.",
            );
            break;
          }
          case "recovery": {
            const result = await platformApi.ownerRecovery(id, reason);
            setNotice(result.detail);
            break;
          }
          case "revoke": {
            const result = await platformApi.ownerRevokeSessions(id, reason);
            setNotice(result.detail);
            break;
          }
        }
        setAction(null);
        refreshAll();
      } catch (caught) {
        // Surfaced, never swallowed: an operator told "done" when the engine
        // refused is worse off than one told what failed.
        setActionError(describeError(caught, "The action could not be completed."));
      } finally {
        setBusy(false);
      }
    },
    [action, id, refreshAll],
  );

  const applyPlan = useCallback(
    async (body: Record<string, unknown>, message: string) => {
      setPlanBusy(true);
      setNotice(null);
      try {
        await platformApi.setPlan(id, body);
        setNotice(message);
        refreshAll();
      } catch (caught) {
        setActionError(describeError(caught, "The plan change was refused."));
      } finally {
        setPlanBusy(false);
      }
    },
    [id, refreshAll],
  );

  if (tenant.loading) return <Spinner label="Loading organization" />;
  if (tenant.error || !tenant.data) {
    return <ErrorNote message={tenant.error ?? "Not found."} onRetry={tenant.reload} />;
  }

  const org = tenant.data;
  const ownerRecord: PlatformOwner | null = owner.data?.owner ?? null;
  const pending = org.status === "pending";
  const suspended = org.status === "suspended";

  return (
    <>
      <div className="mb-3">
        <Link
          href="/platform/organizations"
          className="inline-flex items-center gap-1 text-xs font-medium"
          style={{ color: "var(--pf-text-muted)" }}
        >
          <ArrowLeft className="h-3 w-3" aria-hidden="true" />
          All organizations
        </Link>
      </div>

      <PageHeader
        title={org.name}
        description={`${org.slug} · created ${formatDateTime(org.created_at)}`}
        actions={<Badge status={org.status} />}
      />

      {notice && (
        <div className="mb-3">
          <SuccessNote message={notice} />
        </div>
      )}
      {actionError && !action && (
        <div className="mb-3">
          <ErrorNote message={actionError} />
        </div>
      )}

      {/* ── summary ───────────────────────────────────────────────────── */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card>
          <p className="pf-label">Domains</p>
          <p className="pf-num mt-1 text-xl font-semibold">{org.domain_count}</p>
        </Card>
        <Card>
          <p className="pf-label">Mailboxes</p>
          <p className="pf-num mt-1 text-xl font-semibold">{org.mailbox_count}</p>
        </Card>
        <Card>
          <p className="pf-label">Members</p>
          <p className="pf-num mt-1 text-xl font-semibold">{org.member_count}</p>
        </Card>
        <Card>
          <p className="pf-label">Outbound</p>
          <div className="mt-1.5">
            <Badge tone={org.can_send_outbound ? "ok" : "danger"}>
              {org.can_send_outbound ? "Enabled" : "Disabled"}
            </Badge>
          </div>
          {org.outbound_disabled_at && (
            <p className="mt-1 text-xs pf-faint">
              since {formatRelative(org.outbound_disabled_at)}
            </p>
          )}
        </Card>
      </section>

      {/* ── lifecycle ─────────────────────────────────────────────────── */}
      <section className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <h2 className="mb-1 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Lifecycle
          </h2>
          <p className="mb-3 text-xs pf-muted">
            {org.approved_at
              ? `Approved ${formatDateTime(org.approved_at)}${
                  org.approved_by ? ` by ${org.approved_by}` : ""
                }.`
              : "Not yet approved for mail."}
          </p>
          {org.review_reason && (
            <p className="mb-3 text-xs pf-muted">
              Reason on record: {org.review_reason}
            </p>
          )}

          <div className="flex flex-wrap gap-2">
            {pending && (
              <>
                <button
                  type="button"
                  className="pf-btn pf-btn-primary"
                  onClick={() => setAction({ kind: "approve" })}
                >
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Approve
                </button>
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost"
                  onClick={() => setAction({ kind: "reject" })}
                >
                  <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
                  Reject
                </button>
              </>
            )}
            {!pending && !suspended && (
              <button
                type="button"
                className="pf-btn pf-btn-danger"
                onClick={() => setAction({ kind: "suspend" })}
              >
                <Ban className="h-3.5 w-3.5" aria-hidden="true" />
                Suspend
              </button>
            )}
            {suspended && (
              <button
                type="button"
                className="pf-btn pf-btn-primary"
                onClick={() => setAction({ kind: "activate" })}
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                Reactivate
              </button>
            )}
            <button
              type="button"
              className={`pf-btn ${org.outbound_disabled ? "pf-btn-primary" : "pf-btn-ghost"}`}
              onClick={() =>
                setAction({ kind: "outbound", enable: org.outbound_disabled })
              }
            >
              {org.outbound_disabled ? (
                <Send className="h-3.5 w-3.5" aria-hidden="true" />
              ) : (
                <ShieldOff className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {org.outbound_disabled ? "Enable outbound" : "Disable outbound"}
            </button>
          </div>
        </Card>

        {/* ── plan ───────────────────────────────────────────────────── */}
        <Card>
          <h2 className="mb-1 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Plan &amp; trial
          </h2>
          <p className="mb-3 text-xs pf-muted">
            {org.subscription
              ? `${org.subscription.plan?.display_name ?? org.subscription.plan?.tier ?? "—"} · ${
                  org.subscription.status ?? "—"
                }${
                  org.subscription.trial_ends_at
                    ? ` · trial ends ${formatDateTime(org.subscription.trial_ends_at)}`
                    : ""
                }`
              : "No subscription record."}
          </p>

          <div className="space-y-3">
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-[10rem] flex-1">
                <label htmlFor="pf-plan" className="pf-label">
                  Assign plan
                </label>
                <select
                  id="pf-plan"
                  className="pf-select mt-1"
                  value={planTier}
                  onChange={(event) => setPlanTier(event.target.value)}
                >
                  <option value="">Select a plan…</option>
                  {org.available_plans.map((plan) => (
                    <option key={plan.tier} value={plan.tier}>
                      {plan.display_name}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                className="pf-btn pf-btn-ghost"
                disabled={!planTier || planBusy}
                onClick={() =>
                  applyPlan({ plan_tier: planTier }, `Plan changed to ${planTier}.`)
                }
              >
                Apply
              </button>
            </div>

            <div className="flex flex-wrap items-end gap-2">
              <div className="w-28">
                <label htmlFor="pf-trial" className="pf-label">
                  Extend trial
                </label>
                <input
                  id="pf-trial"
                  className="pf-input mt-1 pf-num"
                  type="number"
                  min={1}
                  max={365}
                  value={trialDays}
                  onChange={(event) => setTrialDays(event.target.value)}
                />
              </div>
              <button
                type="button"
                className="pf-btn pf-btn-ghost"
                disabled={planBusy || !trialDays}
                onClick={() =>
                  applyPlan(
                    { extend_trial_days: Number(trialDays) },
                    `Trial extended by ${trialDays} days.`,
                  )
                }
              >
                Extend
              </button>
            </div>
          </div>
        </Card>
      </section>

      {/* ── primary owner ─────────────────────────────────────────────── */}
      <section className="mt-4">
        <Card>
          <h2 className="mb-1 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Primary Owner
          </h2>
          <p className="mb-3 text-xs pf-muted">
            The account that created this organization. Recovery sends the owner
            a link — it never reveals or replaces their password, and it gives
            no access to their mailbox.
          </p>

          {owner.loading ? (
            <Spinner label="Loading owner" />
          ) : !ownerRecord ? (
            <EmptyState
              title="No owner account on record"
              detail={owner.data?.detail ?? "The owner account may have been deleted."}
            />
          ) : (
            <>
              <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <Field label="Name" value={ownerRecord.full_name || "—"} />
                <Field label="Email" value={ownerRecord.email} />
                <Field
                  label="Account"
                  value={<Badge tone={ownerRecord.is_active ? "ok" : "danger"}>
                    {ownerRecord.is_active ? "Active" : "Disabled"}
                  </Badge>}
                />
                <Field
                  label="Email verified"
                  value={<Badge tone={ownerRecord.email_verified ? "ok" : "warn"}>
                    {ownerRecord.email_verified ? "Verified" : "Unverified"}
                  </Badge>}
                />
                <Field
                  label="Two-factor"
                  value={<Badge tone={ownerRecord.two_factor_enabled ? "ok" : "neutral"}>
                    {ownerRecord.two_factor_enabled ? "Enabled" : "Not enabled"}
                  </Badge>}
                />
                <Field label="Last login" value={formatRelative(ownerRecord.last_login)} />
              </dl>

              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost"
                  onClick={() => setAction({ kind: "recovery" })}
                >
                  <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                  Send password recovery
                </button>
                <button
                  type="button"
                  className="pf-btn pf-btn-ghost"
                  onClick={() => setAction({ kind: "revoke" })}
                >
                  <ShieldOff className="h-3.5 w-3.5" aria-hidden="true" />
                  Revoke owner sessions
                </button>
              </div>
            </>
          )}
        </Card>
      </section>

      {/* ── domains, members, activity ────────────────────────────────── */}
      <section className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <h2 className="mb-2 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Domains
          </h2>
          {org.domains.length === 0 ? (
            <div className="pf-card">
              <EmptyState title="No domains" />
            </div>
          ) : (
            <DataTable columns={["Domain", "Status", "Added"]}>
              {org.domains.map((domain) => (
                <tr key={domain.id}>
                  <td style={{ color: "var(--pf-text)" }}>{domain.name}</td>
                  <td>
                    <Badge status={domain.status} />
                  </td>
                  <td className="pf-muted whitespace-nowrap">
                    {formatDateTime(domain.created_at)}
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </div>

        <div>
          <h2 className="mb-2 text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Members
          </h2>
          {org.members.length === 0 ? (
            <div className="pf-card">
              <EmptyState title="No active members" />
            </div>
          ) : (
            <DataTable columns={["Member", "Role", "Joined"]}>
              {org.members.map((member) => (
                <tr key={member.id}>
                  <td>
                    <div style={{ color: "var(--pf-text)" }}>{member.email}</div>
                    {member.full_name && (
                      <div className="text-xs pf-faint">{member.full_name}</div>
                    )}
                  </td>
                  <td>
                    <Badge tone="neutral">{humanise(member.role)}</Badge>
                  </td>
                  <td className="pf-muted whitespace-nowrap">
                    {formatDateTime(member.joined_at)}
                  </td>
                </tr>
              ))}
            </DataTable>
          )}
        </div>
      </section>

      <section className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold" style={{ color: "var(--pf-text)" }}>
            Recent activity
          </h2>
          <Link
            href={`/platform/logs?tenant=${org.id}`}
            className="text-xs font-medium"
            style={{ color: "var(--pf-accent-text)" }}
          >
            All logs
          </Link>
        </div>
        {org.recent_logs.length === 0 ? (
          <div className="pf-card">
            <EmptyState title="No recorded activity" />
          </div>
        ) : (
          <DataTable columns={["Event", "Result", "Source", "IP", "When"]}>
            {org.recent_logs.map((log, index) => (
              <tr key={`${log.created_at}-${index}`}>
                <td style={{ color: "var(--pf-text)" }}>{humanise(log.event_type)}</td>
                <td>
                  <Badge status={log.result} />
                </td>
                <td className="pf-muted">{log.source || "—"}</td>
                <td className="pf-muted pf-num">{log.ip_address ?? "—"}</td>
                <td className="pf-muted whitespace-nowrap">
                  {formatDateTime(log.created_at)}
                </td>
              </tr>
            ))}
          </DataTable>
        )}
      </section>

      <ConfirmDialog
        open={action !== null}
        title={dialogTitle(action)}
        body={dialogBody(action, org.name, ownerRecord?.email ?? org.owner_email)}
        confirmLabel={dialogConfirm(action)}
        destructive={
          action?.kind === "suspend" ||
          action?.kind === "reject" ||
          action?.kind === "revoke" ||
          (action?.kind === "outbound" && !action.enable)
        }
        requireReason={action?.kind !== "approve" && action?.kind !== "activate"}
        busy={busy}
        error={actionError}
        onConfirm={run}
        onCancel={() => {
          setAction(null);
          setActionError(null);
        }}
      />
    </>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <dt className="pf-label">{label}</dt>
      <dd className="mt-1 text-sm" style={{ color: "var(--pf-text)" }}>
        {value}
      </dd>
    </div>
  );
}

function dialogTitle(action: Action): string {
  switch (action?.kind) {
    case "approve":
      return "Approve organization";
    case "reject":
      return "Reject organization";
    case "suspend":
      return "Suspend organization";
    case "activate":
      return "Reactivate organization";
    case "outbound":
      return action.enable ? "Enable outbound mail" : "Disable outbound mail";
    case "recovery":
      return "Send password recovery";
    case "revoke":
      return "Revoke owner sessions";
    default:
      return "";
  }
}

function dialogConfirm(action: Action): string {
  switch (action?.kind) {
    case "approve":
      return "Approve";
    case "reject":
      return "Reject";
    case "suspend":
      return "Suspend";
    case "activate":
      return "Reactivate";
    case "outbound":
      return action.enable ? "Enable sending" : "Disable sending";
    case "recovery":
      return "Send recovery email";
    case "revoke":
      return "Revoke sessions";
    default:
      return "Confirm";
  }
}

function dialogBody(action: Action, name: string, ownerEmail: string): React.ReactNode {
  switch (action?.kind) {
    case "approve":
      return <><strong>{name}</strong> will be able to provision domains and send mail.</>;
    case "reject":
      return (
        <>
          <strong>{name}</strong> will be refused. The reason is shown to the owner.
        </>
      );
    case "suspend":
      return (
        <>
          <strong>{name}</strong> will stop sending and receiving mail. Existing
          mailboxes are not deleted, and the action is reversible.
        </>
      );
    case "activate":
      return <><strong>{name}</strong> will return to normal operation.</>;
    case "outbound":
      return action.enable ? (
        <>
          <strong>{name}</strong> will be able to send mail again. Inbound delivery
          is unaffected either way.
        </>
      ) : (
        <>
          <strong>{name}</strong> will stop sending mail immediately. It keeps
          receiving, and the console stays usable — this is the lighter of the two
          abuse responses.
        </>
      );
    case "recovery":
      return (
        <>
          A password-reset link will be emailed to <strong>{ownerEmail}</strong>.
          You will not see the link, and their current password is unchanged until
          they use it.
        </>
      );
    case "revoke":
      return (
        <>
          Every session belonging to <strong>{ownerEmail}</strong> will be signed
          out. Access tokens already issued remain valid until they expire.
        </>
      );
    default:
      return null;
  }
}
