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

export interface Domain {
  id: string;
  domain: string;
  status: DomainStatus;
  dns_health_score: number;
  dkim_selector: string;
  dkim_public_key: string;
  added_at: string;
  verified_at: string | null;
}

export interface Mailbox {
  id: string;
  full_name: string;
  email: string;
  local_part: string;
  domain: string;
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

export interface HealthStatus {
  status: "ok" | "degraded" | "error";
  checks: {
    db: "ok" | "error";
    redis: "ok" | "error";
    mail_engine: "ok" | "error" | "unknown";
  };
  version: string;
}
