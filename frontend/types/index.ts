export type TenantStatus =
  | "trial"
  | "active"
  | "past_due"
  | "suspended"
  | "cancelled";

export type DomainStatus = "pending" | "active" | "warning" | "failed" | "paused";

export type MailboxStatus = "active" | "disabled" | "suspended";

export type MemberRole = "owner" | "admin" | "support" | "read_only";

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: TenantStatus;
  plan: string;
}

export type DomainOwnershipStatus = "pending" | "verified";

export interface Domain {
  id: string;
  domain: string;
  status: DomainStatus;
  dns_health_score: number;
  dkim_selector: string;
  dkim_public_key: string;
  /**
   * Ownership proof. A domain stays `pending` until its verification TXT
   * record resolves, and nothing is provisioned for it until then.
   */
  ownership_status: DomainOwnershipStatus;
  ownership_verified: boolean;
  ownership_verified_at: string | null;
  verification_record_type: string;
  verification_record_name: string;
  verification_record_value: string;
  verification_instructions: string;
  verification_last_checked_at: string | null;
  verification_last_error: string;
  added_at: string;
  verified_at: string | null;
}

export interface Mailbox {
  id: string;
  full_name: string;
  email: string;
  local_part: string;
  domain: string;
  kind: "personal" | "team_box";
  status: MailboxStatus;
  quota_mb: number;
  storage_used_mb: number;
  last_login: string | null;
  created_at: string;
}

export interface DnsRecord {
  type: string;
  host: string;
  value: string;
  status: "verified" | "pending" | "missing" | "failed";
}

/**
 * Public health payload. Deliberately minimal: component-level infrastructure
 * state is operator-only and served from an internal endpoint the browser
 * cannot reach.
 */
export interface HealthStatus {
  status: "ok" | "unavailable";
  service: string;
}
