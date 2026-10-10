"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  KeyRound,
  Search,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserCog,
} from "lucide-react";

import { apiRequest } from "@/lib/api";
import { useAuth } from "@/contexts/auth-context";
import { AstraResourceDialog } from "@/components/workspace/astra-resource-dialog";
import {
  PortalButton,
  PortalCard,
  PortalEmptyState,
  PortalNotice,
  PortalPageHeading,
  PortalSkeleton,
} from "@/components/workspace/premium-ui";

interface Mailbox {
  id: string;
  email: string;
  full_name: string;
  kind: "personal" | "team_box";
  status: string;
  mail_service_ready: boolean;
}

interface Delegation {
  id: string;
  target_mailbox_id: string;
  target_email: string;
  target_name: string;
  delegate_mailbox_id: string;
  delegate_email: string;
  delegate_name: string;
  can_read: boolean;
  can_manage: boolean;
  can_send_as: boolean;
  can_send_on_behalf: boolean;
  active: boolean;
}

type PermissionKey =
  | "can_read"
  | "can_manage"
  | "can_send_as"
  | "can_send_on_behalf";

const permissionLabels: Array<{
  key: PermissionKey;
  label: string;
  hint: string;
}> = [
  { key: "can_read", label: "Read", hint: "Open and read this mailbox." },
  { key: "can_manage", label: "Manage", hint: "Move, label, delete and organize mail. Requires Read." },
  { key: "can_send_as", label: "Send As", hint: "Send with the mailbox address as the visible sender." },
  { key: "can_send_on_behalf", label: "Send on behalf", hint: "Send on behalf of the mailbox owner." },
];

function errorText(value: unknown) {
  if (!value) return "";
  if (Array.isArray(value)) return value.map(String).join(" ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function DelegationPageContent() {
  const routeParams = useSearchParams();
  const requestedSearch = routeParams.get("q") || "";
  const { tenant, user } = useAuth();
  const [myRole, setMyRole] = useState(tenant?.role || "");
  const canAdmin = myRole === "owner" || myRole === "admin";
  const [delegations, setDelegations] = useState<Delegation[]>([]);
  const [query, setQuery] = useState("");
  const filteredDelegations = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return delegations;
    return delegations.filter(row =>
      [row.target_email, row.target_name, row.delegate_email, row.delegate_name]
        .some(value => value?.toLocaleLowerCase().includes(needle))
    );
  },[delegations,query]);


  const [mailboxes, setMailboxes] = useState<Mailbox[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [targetId, setTargetId] = useState("");
  const [delegateId, setDelegateId] = useState("");
  const [permissions, setPermissions] = useState<Record<PermissionKey, boolean>>({
    can_read: true,
    can_manage: false,
    can_send_as: false,
    can_send_on_behalf: false,
  });
  const [busy, setBusy] = useState(false);
  const [rowBusy, setRowBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState(false);
  const [createErrors, setCreateErrors] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (!tenant?.id) return;
    let alive = true;
    apiRequest(`/api/workspaces/${tenant.id}/stats/`)
      .then(async response => response.ok ? response.json() : null)
      .then(data => {
        if (alive && typeof data?.my_role === "string") setMyRole(data.my_role);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [tenant?.id]);

  useEffect(() => {
    // Sync incoming query after mount (no render-cascade state update).
    const id = window.setTimeout(() => setQuery(requestedSearch), 0);
    return () => window.clearTimeout(id);
  }, [requestedSearch]);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [delegationResponse, mailboxResponse] = await Promise.all([
        apiRequest("/api/delegations/"),
        apiRequest("/api/mailboxes/"),
      ]);

      if (delegationResponse.ok) {
        setDelegations(await delegationResponse.json());
      } else {
        const data = await delegationResponse.json().catch(() => null);
        setLoadError(
          delegationResponse.status === 403
            ? "Only organization owners and admins can manage mailbox delegation."
            : data?.detail ?? "Delegation could not be loaded."
        );
      }

      if (mailboxResponse.ok) {
        const rows = (await mailboxResponse.json()) as Mailbox[];
        setMailboxes(
          rows.filter(
            (mailbox) =>
              mailbox.kind === "personal" &&
              mailbox.status === "active" &&
              mailbox.mail_service_ready
          )
        );
      }
    } catch {
      setLoadError("Delegation could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchAll(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchAll]);

  const chosenTarget =
    mailboxes.some((mailbox) => mailbox.id === targetId)
      ? targetId
      : mailboxes[0]?.id || "";

  const availableDelegates = useMemo(
    () => mailboxes.filter((mailbox) => mailbox.id !== chosenTarget),
    [chosenTarget, mailboxes]
  );

  const chosenDelegate =
    availableDelegates.some((mailbox) => mailbox.id === delegateId)
      ? delegateId
      : availableDelegates[0]?.id || "";

  function setMessage(text: string, isFailed = false) {
    setNotice(text);
    setFailed(isFailed);
  }

  function toggleCreatePermission(key: PermissionKey, checked: boolean) {
    setPermissions((current) => {
      const next = { ...current, [key]: checked };
      if (key === "can_read" && !checked) next.can_manage = false;
      if (key === "can_manage" && checked) next.can_read = true;
      return next;
    });
  }

  async function createDelegation(event: React.FormEvent) {
    event.preventDefault();
    setCreateErrors({});
    setBusy(true);
    try {
      const response = await apiRequest("/api/delegations/", {
        method: "POST",
        body: JSON.stringify({
          target_mailbox_id: chosenTarget,
          delegate_mailbox_id: chosenDelegate,
          ...permissions,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setCreateErrors(
          typeof data === "object" && data !== null
            ? data
            : { detail: "Delegation could not be created." }
        );
        return;
      }

      setCreateOpen(false);
      setPermissions({
        can_read: true,
        can_manage: false,
        can_send_as: false,
        can_send_on_behalf: false,
      });
      setMessage("Delegation created.");
      await fetchAll();
    } finally {
      setBusy(false);
    }
  }

  async function patchDelegation(
    row: Delegation,
    patch: Partial<Pick<
      Delegation,
      PermissionKey | "active"
    >>
  ) {
    const next = { ...row, ...patch };
    if (patch.can_read === false && next.can_manage) next.can_manage = false;
    if (patch.can_manage === true) next.can_read = true;
    if (
      next.active &&
      !next.can_read &&
      !next.can_manage &&
      !next.can_send_as &&
      !next.can_send_on_behalf
    ) {
      setMessage("An active delegation must keep at least one permission.", true);
      return;
    }

    setRowBusy(row.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/delegations/${row.id}/`, {
        method: "PATCH",
        body: JSON.stringify({
          can_read: next.can_read,
          can_manage: next.can_manage,
          can_send_as: next.can_send_as,
          can_send_on_behalf: next.can_send_on_behalf,
          active: next.active,
        }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        setMessage(data?.detail ? errorText(data.detail) : "Delegation could not be updated.", true);
        return;
      }
      setDelegations((current) =>
        current.map((item) => item.id === row.id ? data : item)
      );
      setMessage("Delegation updated.");
    } finally {
      setRowBusy("");
    }
  }

  async function removeDelegation(row: Delegation) {
    if (!window.confirm(`Remove ${row.delegate_email}'s access to ${row.target_email}?`)) return;
    setRowBusy(row.id);
    setMessage("");
    try {
      const response = await apiRequest(`/api/delegations/${row.id}/`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setMessage(data?.detail ?? "Delegation could not be removed.", true);
        return;
      }
      setDelegations((current) => current.filter((item) => item.id !== row.id));
      setMessage("Delegation removed.");
    } finally {
      setRowBusy("");
    }
  }

  return (
    <div className="portal-page astra-resource-page astra-routing-page astra-collaboration-page">
      <PortalPageHeading
        title="Delegation"
        description="Give one personal mailbox controlled access to another personal mailbox without sharing passwords."
        actions={
          <PortalButton
            type="button"
            disabled={!canAdmin || !user?.email_verified || mailboxes.length < 2}
            onClick={() => {
              setCreateErrors({});
              setCreateOpen(true);
            }}
          >
            <Plus className="h-4 w-4" />
            Add delegation
          </PortalButton>
        }
      />

      <div className="astra-resource-summary" aria-label="Delegation statistics">
        <div><span>Delegations</span><strong>{loading ? "—" : delegations.length}</strong></div>
        <div><span>Active grants</span><strong>{loading ? "—" : delegations.filter(row => row.active).length}</strong></div>
        <div><span>Available mailboxes</span><strong>{loading ? "—" : mailboxes.length}</strong></div>
      </div>
      <div className="mb-5">
        <PortalNotice tone="info">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Delegates sign in with their own PostBox account. The target mailbox password is never shared or changed.
          </span>
        </PortalNotice>
      </div>

      <AstraResourceDialog
        open={createOpen && canAdmin}
        busy={busy}
        title="Add mailbox delegation"
        description="Select two personal mailboxes and grant only the permissions needed."
        onDismiss={() => { setCreateOpen(false); setCreateErrors({}); }}
      >
          <form onSubmit={createDelegation}>
            {errorText(createErrors.detail) && (
              <div className="mb-4">
                <PortalNotice tone="danger">{errorText(createErrors.detail)}</PortalNotice>
              </div>
            )}

            <div className="portal-form-grid">
              <div className="portal-field">
                <label htmlFor="astra-delegation-target">Mailbox to delegate</label>
                <select
                  id="astra-delegation-target"
                  value={chosenTarget}
                  onChange={(event) => {
                    setTargetId(event.target.value);
                    if (event.target.value === delegateId) setDelegateId("");
                  }}
                  required
                >
                  {mailboxes.map((mailbox) => (
                    <option key={mailbox.id} value={mailbox.id}>
                      {mailbox.full_name ? `${mailbox.full_name} — ${mailbox.email}` : mailbox.email}
                    </option>
                  ))}
                </select>
                {errorText(createErrors.target_mailbox_id) && (
                  <div className="portal-field-error">
                    {errorText(createErrors.target_mailbox_id)}
                  </div>
                )}
              </div>

              <div className="portal-field">
                <label htmlFor="astra-delegation-recipient">Delegate mailbox</label>
                <select
                  id="astra-delegation-recipient"
                  value={chosenDelegate}
                  onChange={(event) => setDelegateId(event.target.value)}
                  required
                >
                  {availableDelegates.map((mailbox) => (
                    <option key={mailbox.id} value={mailbox.id}>
                      {mailbox.full_name ? `${mailbox.full_name} — ${mailbox.email}` : mailbox.email}
                    </option>
                  ))}
                </select>
                {errorText(createErrors.delegate_mailbox_id) && (
                  <div className="portal-field-error">
                    {errorText(createErrors.delegate_mailbox_id)}
                  </div>
                )}
              </div>

              <div className="portal-field full">
                <label>Permissions</label>
                <div className="grid gap-3 md:grid-cols-2">
                  {permissionLabels.map((permission) => (
                    <label
                      key={permission.key}
                      className="portal-choice"
                      data-active={permissions[permission.key]}
                    >
                      <input
                        type="checkbox"
                        checked={permissions[permission.key]}
                        onChange={(event) =>
                          toggleCreatePermission(permission.key, event.target.checked)
                        }
                      />
                      <span>
                        <strong>{permission.label}</strong>
                        <small className="block">{permission.hint}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            <div className="portal-detail-actions">
              <PortalButton
                type="submit"
                disabled={
                  busy ||
                  !chosenTarget ||
                  !chosenDelegate ||
                  !Object.values(permissions).some(Boolean)
                }
              >
                {busy ? "Saving…" : "Add delegation"}
              </PortalButton>
              <PortalButton
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => setCreateOpen(false)}
              >
                Cancel
              </PortalButton>
            </div>
          </form>
      </AstraResourceDialog>

      {notice && (
        <div className="mb-5">
          <PortalNotice tone={failed ? "danger" : "success"}>{notice}</PortalNotice>
        </div>
      )}

      {loadError && (
        <div className="mb-5">
          <PortalNotice tone="danger">{loadError}</PortalNotice>
        </div>
      )}

      <PortalCard className="portal-management-card" bodyClassName="!p-0">
        <div className="portal-management-toolbar">
          <div className="portal-management-search">
            <Search className="h-4 w-4" aria-hidden="true"/>
            <input aria-label="Search delegations" placeholder="Search delegations…" type="search" value={query} onChange={event=>setQuery(event.target.value)}/>
          </div>
          <button
            type="button"
            className="portal-icon-button"
            aria-label="Refresh Delegation"
            onClick={() => void fetchAll()}
            disabled={loading}
          >
            <RefreshCw className={"h-4 w-4 " + (loading ? "animate-spin" : "")} />
          </button>
        </div>

        {loading ? (
          <div className="p-5">
            <PortalSkeleton className="mb-3 h-14 w-full" />
            <PortalSkeleton className="h-14 w-full" />
          </div>
        ) : filteredDelegations.length === 0 ? (
          <PortalEmptyState
            title="No mailbox delegation"
            description="Add a delegate when one personal mailbox needs controlled access to another."
          />
        ) : (
          <div className="portal-management-table-wrap">
            <table className="portal-management-table">
              <thead>
                <tr>
                  <th>Mailbox</th>
                  <th>Delegate</th>
                  <th>Read</th>
                  <th>Manage</th>
                  <th>Send As</th>
                  <th>On behalf</th>
                  <th>Active</th>
                  <th className="text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredDelegations.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <strong>{row.target_name || row.target_email}</strong>
                      <small className="block">{row.target_email}</small>
                    </td>
                    <td>
                      <strong>{row.delegate_name || row.delegate_email}</strong>
                      <small className="block">{row.delegate_email}</small>
                    </td>
                    {permissionLabels.map((permission) => (
                      <td key={permission.key}>
                        <input
                          type="checkbox"
                          checked={row[permission.key]}
                          disabled={!canAdmin || rowBusy === row.id}
                          onChange={(event) =>
                            void patchDelegation(row, {
                              [permission.key]: event.target.checked,
                            })
                          }
                          aria-label={`${permission.label} for ${row.delegate_email}`}
                        />
                      </td>
                    ))}
                    <td>
                      <input
                        type="checkbox"
                        checked={row.active}
                        disabled={!canAdmin || rowBusy === row.id}
                        onChange={(event) =>
                          void patchDelegation(row, { active: event.target.checked })
                        }
                        aria-label={`Delegation active for ${row.delegate_email}`}
                      />
                    </td>
                    <td>
                      <div className="portal-inline-actions">
                        <button
                          type="button"
                          className="portal-action-button danger"
                          disabled={!canAdmin || rowBusy === row.id}
                          onClick={() => void removeDelegation(row)}
                          aria-label={`Remove delegation for ${row.delegate_email}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </PortalCard>

      <div className="mt-4">
        <PortalNotice tone="info">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Delegation is personal-mailbox access. TeamBox membership and device account switching remain separate features.
          </span>
        </PortalNotice>
      </div>
    </div>
  );
}

/** Suspense boundary required by Next.js useSearchParams during static builds. */
export default function DelegationPage() {
  return <Suspense fallback={null}><DelegationPageContent /></Suspense>;
}
