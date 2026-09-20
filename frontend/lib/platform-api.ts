/**
 * Typed access to the Platform Console API.
 *
 * Thin on purpose: `lib/api` already handles the access token, the silent
 * refresh and the HttpOnly refresh cookie, and a second HTTP layer would be a
 * second place for those rules to drift. What lives here is the shapes and the
 * query-string building, so a page can ask for "page 2 of suspended mailboxes"
 * without assembling URLs by hand.
 */
import { api, ApiError } from "./api";

export { ApiError };

export interface Paged<T> {
  results: T[];
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
}

export interface TenantBrief {
  id: string;
  name: string;
  slug: string;
  status: string;
}

export interface PlatformDomain {
  id: string;
  domain: string;
  tenant: TenantBrief | null;
  status: string;
  ownership_status: string;
  ownership_verified_at: string | null;
  verification_last_checked_at: string | null;
  verification_last_error: string;
  ownership_recheck_failures: number;
  dns_health_score: number;
  dkim_selector: string;
  dkim_configured: boolean;
  mail_engine_provisioned: boolean;
  mail_engine_error: string;
  added_at: string | null;
  verified_at: string | null;
}

export interface PlatformMailbox {
  id: string;
  email: string;
  full_name: string;
  tenant: TenantBrief | null;
  domain: string | null;
  status: string;
  quota_mb: number;
  storage_used_mb: number;
  mail_engine_provisioned: boolean;
  mail_engine_error: string;
  last_login: string | null;
  created_at: string | null;
}

export interface PlatformRoute {
  id: string;
  kind: "alias" | "forwarding";
  source: string | null;
  destination: string | null;
  tenant: TenantBrief | null;
  domain?: string | null;
  status: string;
  keep_copy?: boolean;
  mail_engine_provisioned: boolean;
  created_at: string | null;
  external_destination: boolean;
}

export interface PlatformQueueMessage {
  id: string;
  tenant: TenantBrief | null;
  sender: string;
  recipient: string;
  subject: string;
  status: string;
  reason: string;
  retry_count: number;
  queued_at: string | null;
  last_retry: string | null;
  next_retry: string | null;
}

export interface PlatformQuarantineMessage {
  id: string;
  tenant: TenantBrief | null;
  sender: string;
  recipient: string;
  subject: string;
  spam_score: number;
  status: string;
  received_at: string | null;
  actioned_at: string | null;
}

export interface PlatformMailLog {
  id: string;
  tenant: TenantBrief | null;
  event_type: string;
  source: string;
  result: string;
  ip_address: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface PlatformAuditRow {
  id: string;
  actor_email: string;
  action: string;
  tenant: TenantBrief | null;
  target_label: string;
  result: string;
  reason: string;
  ip_address: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface PlatformPlan {
  id: string;
  tier: string;
  display_name: string;
  price_monthly: number;
  is_active: boolean;
  subscriber_count: number;
  limits: Record<string, number>;
  features: Record<string, boolean>;
}

export interface HealthComponent {
  name: string;
  status: "ok" | "error" | "unknown";
  latency_ms: number | null;
  detail: string;
}

export interface PlatformHealth {
  overall: string;
  checked_at: string;
  components: HealthComponent[];
  note: string;
}

export interface PlatformBackups {
  platform_backups: {
    status: string;
    observable_from_application: boolean;
    detail: string;
    where_to_look: string[];
  };
  tenant_backup_jobs: {
    supported: boolean;
    detail: string;
    recent: Array<{
      id: string;
      tenant: string | null;
      scope: string;
      status: string;
      error_message: string;
      created_at: string | null;
    }>;
    counts: Record<string, number>;
  };
}

export interface PlatformOwner {
  id: string;
  full_name: string;
  email: string;
  is_active: boolean;
  email_verified: boolean;
  two_factor_enabled: boolean;
  last_login: string | null;
  created_at: string | null;
}

export interface SearchHit {
  type: string;
  id: string;
  label: string;
  sublabel: string;
  status: string;
  href: string;
}

export type QueryValue = string | number | boolean | undefined | null;

/** Build a query string, dropping empty values so filters read cleanly. */
export function qs(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const P = "/api/platform";

/**
 * A body on a DELETE.
 *
 * `api.delete` takes a RequestInit rather than a body, because most DELETEs do
 * not have one. The reverse actions here do: every oversight action requires a
 * reason, including the one that undoes it, so the audit trail records why
 * something was re-enabled as well as why it was disabled.
 */
function withBody(body: unknown): RequestInit {
  return {
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  };
}

export const platformApi = {
  stats: () => api.get<Record<string, unknown>>(`${P}/stats/`),

  tenants: (params: Record<string, QueryValue> = {}) =>
    api.get<unknown>(`${P}/tenants/${qs(params)}`),
  tenant: (id: string) => api.get<Record<string, unknown>>(`${P}/tenants/${id}/`),
  pendingTenants: () => api.get<unknown>(`${P}/tenants/pending/`),

  owner: (tenantId: string) =>
    api.get<{ owner: PlatformOwner | null; detail?: string }>(
      `${P}/tenants/${tenantId}/owner/`,
    ),
  ownerRecovery: (tenantId: string, reason: string) =>
    api.post<{ detail: string; delivered: boolean }>(
      `${P}/tenants/${tenantId}/owner/password-recovery/`,
      { reason },
    ),
  ownerRevokeSessions: (tenantId: string, reason: string) =>
    api.post<{ detail: string; sessions_revoked: number }>(
      `${P}/tenants/${tenantId}/owner/revoke-sessions/`,
      { reason },
    ),

  domains: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformDomain>>(`${P}/domains/${qs(params)}`),
  mailboxes: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformMailbox>>(`${P}/mailboxes/${qs(params)}`),
  aliases: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformRoute>>(`${P}/aliases/${qs(params)}`),
  forwarding: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformRoute>>(`${P}/forwarding/${qs(params)}`),
  queue: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformQueueMessage>>(`${P}/queue/${qs(params)}`),
  quarantine: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformQuarantineMessage>>(`${P}/quarantine/${qs(params)}`),
  logs: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformMailLog>>(`${P}/logs/${qs(params)}`),
  audit: (params: Record<string, QueryValue> = {}) =>
    api.get<Paged<PlatformAuditRow>>(`${P}/audit/${qs(params)}`),
  auditActions: () => api.get<{ actions: string[] }>(`${P}/audit/actions/`),
  plans: () => api.get<{ results: PlatformPlan[] }>(`${P}/plans/`),
  health: () => api.get<PlatformHealth>(`${P}/health/`),
  backups: () => api.get<PlatformBackups>(`${P}/backups/`),
  search: (q: string) => api.get<{ query: string; results: SearchHit[] }>(
    `${P}/search/${qs({ q })}`,
  ),

  // ── actions ──────────────────────────────────────────────────────────────
  approveTenant: (id: string) => api.post<unknown>(`${P}/tenants/${id}/approve/`, {}),
  rejectTenant: (id: string, reason: string) =>
    api.post<unknown>(`${P}/tenants/${id}/reject/`, { reason }),
  suspendTenant: (id: string, reason: string) =>
    api.post<unknown>(`${P}/tenants/${id}/suspend/`, { reason }),
  activateTenant: (id: string) => api.post<unknown>(`${P}/tenants/${id}/activate/`, {}),
  // One POST carrying the desired state, which is what the endpoint accepts.
  setOutbound: (id: string, enabled: boolean, reason: string) =>
    api.post<unknown>(`${P}/tenants/${id}/outbound/`, { enabled, reason }),
  setPlan: (id: string, body: Record<string, unknown>) =>
    api.post<unknown>(`${P}/tenants/${id}/plan/`, body),

  suspendMailbox: (id: string, reason: string) =>
    api.post<unknown>(`${P}/mailboxes/${id}/suspend/`, { reason }),
  releaseMailbox: (id: string) => api.delete<unknown>(`${P}/mailboxes/${id}/suspend/`),

  setAliasDisabled: (id: string, disabled: boolean, reason: string) =>
    disabled
      ? api.post<unknown>(`${P}/aliases/${id}/disable/`, { reason })
      : api.delete<unknown>(`${P}/aliases/${id}/disable/`, withBody({ reason })),
  setForwardingDisabled: (id: string, disabled: boolean, reason: string) =>
    disabled
      ? api.post<unknown>(`${P}/forwarding/${id}/disable/`, { reason })
      : api.delete<unknown>(`${P}/forwarding/${id}/disable/`, withBody({ reason })),
  quarantineAction: (id: string, release: boolean, reason: string) =>
    release
      ? api.post<unknown>(`${P}/quarantine/${id}/action/`, { reason })
      : api.delete<unknown>(`${P}/quarantine/${id}/action/`, withBody({ reason })),
  cancelQueued: (id: string, reason: string) =>
    api.post<unknown>(`${P}/queue/${id}/cancel/`, { reason }),
};
