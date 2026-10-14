/** Strongly typed Platform Mail Center contract. Every request uses shared platform auth. */
import { api, apiRequest, ApiError } from "./api";

const ROOT = "/api/platform/mail-center/";

export type MailRole = "operations" | "security" | "noreply";
export interface MailCapabilities {
  operations: boolean;
  sending: boolean;
  sensitive_access: boolean;
  reporting: boolean;
}
export interface MailFolder {
  name: string;
  role: string;
  messages: number;
  unseen: number;
}
export interface MailFolderPage {
  mailbox?: string;
  mailbox_role?: string;
  results: MailFolder[];
  has_more: boolean;
}
export interface MailSummary {
  uid: number;
  subject: string;
  from_name: string;
  from_address: string;
  date: string | null;
  seen: boolean;
  has_attachments: boolean;
  size: number;
}
export interface MailMessagePage {
  uid_validity: number;
  folder: string;
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
  results: MailSummary[];
}
export interface MailAttachment {
  part_id: string;
  filename: string;
  content_type: string;
  size: number;
  downloadable: boolean;
}
export interface MailDetail {
  uid: number;
  uid_validity: number;
  folder: string;
  subject: string;
  from: { name: string; address: string };
  to: string[];
  cc: string[];
  reply_to: string[];
  date: string | null;
  message_id: string;
  text: string;
  html: string;
  remote_images_blocked: boolean;
  attachments: MailAttachment[];
}
export interface MailDraftRef {
  uid: number;
  uid_validity: number;
}
export interface MailReplyRef extends MailDraftRef {
  folder: string;
  mode: "reply" | "reply_all";
}
export interface MailCompose {
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  text: string;
  draft?: MailDraftRef;
  reply?: MailReplyRef;
}
export interface MailSendResult {
  sent: boolean;
  from_address: string;
  filed_in_sent: boolean;
  message_id: string;
}
export interface MailDraftResult {
  saved: boolean;
  folder: string;
  uid: number;
  uid_validity: number;
  previous_removed: boolean;
}
export interface MailGrant {
  role: "security" | "noreply";
  access: "read";
  expires_at: string;
}
export interface MailGrantCreated extends MailGrant {
  grant_id: string;
  grantee_id: string;
}
export interface ReceiverState {
  receiver: "legacy" | "system";
  active: boolean;
  status: string;
  last_checked_at: string | null;
  last_success_at: string | null;
}
export interface MailDaily {
  date: string;
  report_count: number;
  message_count?: number;
  spf_pass?: number;
  dkim_pass?: number;
  dmarc_pass?: number;
  successful_sessions?: number;
  failed_sessions?: number;
}
export interface MailReportOverview {
  window: { days: number; start: string; end: string; timezone: "UTC" };
  dmarc: {
    scope: string; report_count: number; message_count: number;
    spf_pass: number; dkim_pass: number; dmarc_pass: number;
    daily: MailDaily[];
  };
  tls: {
    scope: string; report_count: number; successful_sessions: number;
    failed_sessions: number; daily: MailDaily[];
  };
  receivers: { dmarc: ReceiverState[]; tls: ReceiverState[] };
  notice: string;
}
function prefix(role: MailRole): string {
  return role === "operations" ? ROOT : ROOT + "purpose/" + role + "/";
}
function refQuery(folder: string, uidValidity: number): string {
  const qs = new URLSearchParams({ folder, uid_validity: String(uidValidity) });
  return "?" + qs.toString();
}

export const mailCenterApi = {
  capabilities: () => api.get<MailCapabilities>(ROOT + "capabilities/"),
  folders: (role: MailRole) => api.get<MailFolderPage>(prefix(role) + "folders/"),
  messages: (role: MailRole, folder: string, page = 1) =>
    api.get<MailMessagePage>(
      prefix(role) + "messages/?" +
      new URLSearchParams({ folder, page: String(page), page_size: "25" }).toString(),
    ),
  detail: (role: MailRole, uid: number, folder: string, uidValidity: number) =>
    api.get<MailDetail>(
      prefix(role) + "messages/" + uid + "/" + refQuery(folder, uidValidity),
    ),
  async attachment(role: MailRole, uid: number, partId: string, folder: string, uidValidity: number) {
    if (!/^\d{1,3}$/.test(partId)) throw new Error("Unsupported attachment.");
    const response = await apiRequest(
      prefix(role) + "messages/" + uid + "/attachments/" + partId + "/" +
      refQuery(folder, uidValidity),
      { method: "GET" },
    );
    if (!response.ok) throw new ApiError(response.status, "Attachment unavailable.");
    if (response.headers.get("content-type")?.split(";")[0] !== "application/octet-stream") {
      throw new Error("Unexpected attachment content type.");
    }
    const blob = await response.blob();
    if (blob.size > 2 * 1024 * 1024) throw new Error("Attachment exceeds the download limit.");
    return blob;
  },
  send: (data: MailCompose, idempotencyKey: string) =>
    api.post<MailSendResult>(ROOT + "send/", { ...data, idempotency_key: idempotencyKey }),
  saveDraft: (data: MailCompose) =>
    api.post<MailDraftResult>(ROOT + "drafts/", data),
  deleteDraft: (uid: number, uidValidity: number) =>
    api.delete<Record<string, never>>(
      ROOT + "drafts/" + uid + "/?uid_validity=" + uidValidity,
    ),
  grants: () => api.get<{ permissions: MailGrant[] }>(ROOT + "system-access/"),
  grantInventory: () => api.get<{ results: Array<{ grant_id: string; role: "security" | "noreply"; grantee_id: string; grantee_email: string; expires_at: string }>; truncated: boolean }>(ROOT + "system-access/manage/"),
  grant: (granteeId: string, role: "security" | "noreply", durationHours: number) =>
    api.post<MailGrantCreated>(ROOT + "system-access/grants/", {
      grantee_id: granteeId, role, duration_hours: durationHours,
    }),
  revoke: (grantId: string) => api.delete(ROOT + "system-access/grants/" + grantId + "/"),
  reporting: (days: number) =>
    api.get<MailReportOverview>(ROOT + "reports/overview/?days=" + days),
};
