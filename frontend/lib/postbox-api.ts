/**
 * The PostBox API client.
 *
 * WHY THIS DOES NOT USE `lib/api`
 *   That client carries a JWT access token and silently refreshes it against
 *   `/api/auth/refresh/` — the Workspace session. PostBox is authenticated by
 *   a host-scoped HttpOnly session cookie and holds no token at all, so there
 *   is nothing for it to attach and nothing for it to refresh. Sharing the
 *   client would mean a Workspace token being sent to PostBox endpoints, which
 *   is exactly the confusion the two identities exist to avoid.
 *
 *   Everything here is same-origin and relative, so the cookie travels
 *   automatically and the request follows whichever host the console is on.
 */

import { announcePostBoxMailboxChange } from "@/lib/postbox-realtime";

export class PostBoxError extends Error {
  constructor(
    public status: number,
    message: string,
    public fields?: Record<string, string[]>,
  ) {
    super(message);
    this.name = "PostBoxError";
  }
}

const BASE = "/api/postbox";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    // Same-origin is the default, but stating it makes the cookie dependency
    // explicit for anyone reading this later.
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    },
    ...options,
  });

  if (response.status === 204) return {} as T;

  const text = await response.text();
  let body: unknown = {};
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }

  if (!response.ok) {
    const parsed = body as Record<string, unknown>;
    const detail =
      typeof parsed.detail === "string"
        ? parsed.detail
        : firstFieldError(parsed) ?? "Something went wrong.";
    throw new PostBoxError(response.status, detail, fieldErrors(parsed));
  }

  return body as T;
}

function firstFieldError(body: Record<string, unknown>): string | null {
  for (const value of Object.values(body)) {
    if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  }
  return null;
}

function fieldErrors(body: Record<string, unknown>): Record<string, string[]> {
  const result: Record<string, string[]> = {};
  for (const [key, value] of Object.entries(body)) {
    if (Array.isArray(value) && value.every((v) => typeof v === "string")) {
      result[key] = value as string[];
    }
  }
  return result;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Folder names may contain `/`, so each segment is encoded individually. */
function encodeFolder(folder: string): string {
  return folder.split("/").map(encodeURIComponent).join("/");
}

// ── shapes ──────────────────────────────────────────────────────────────────

export interface MailboxProfile {
  id: string;
  email: string;
  full_name: string;
  quota_mb: number;
  organization: string;
  domain: string;
}

export interface SavedPostBoxAccount {
  session_id: string;
  mailbox: MailboxProfile;
  expires_at: string;
  remembered: boolean;
  current: boolean;
}

/** Mail-client settings, served by the backend so there is one source. */
export interface MailClientSettings {
  username: string;
  imap: { server: string; port: number; encryption: string };
  smtp: {
    server: string;
    port: number;
    encryption: string;
    auth_required: boolean;
  };
}

export interface Preferences {
  theme: "light" | "dark" | "system";
  density: "comfortable" | "compact";
  reading_pane: "right" | "bottom" | "off";
  list_view: "conversations" | "messages";
  reader_view: "thread" | "single";
  load_remote_images: boolean;
  messages_per_page: number;
  timezone_name: string;
  notify_in_app: boolean;
  notify_sound: boolean;
  default_identity: string;
}

export interface MailLabel {
  id: string;
  name: string;
  count: number;
  color: string;
}

export interface MessageLabel {
  id: string;
  name: string;
  color: string;
}

export interface Folder {
  name: string;
  role: string;
  color: string | null;
  messages: number;
  unseen: number;
}

export interface MessageSummary {
  uid: number;
  uid_validity: number;
  folder: string;
  message_id: string;
  labels?: MessageLabel[];
  subject: string;
  from: { name: string; address: string };
  to: string[];
  cc: string[];
  date: string;
  size: number;
  seen: boolean;
  flagged: boolean;
  answered: boolean;
  draft: boolean;
  has_attachments: boolean;
  in_reply_to: string;
  references: string[];
}

export interface MessagePage {
  folder: string;
  scope?: "folder" | "all" | "all_with_spam_trash" | "label";
  sort?: "newest" | "oldest";
  uid_validity: number;
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
  results: MessageSummary[];
}

/** Header-only conversation data. Message bodies load through existing message API. */
export interface ConversationMember extends MessageSummary {
  copies: Array<{
    folder: string;
    uid_validity: number;
    uid: number;
    seen: boolean;
    flagged: boolean;
  }>;
}

export interface ConversationSummary {
  id: string;
  subject: string;
  latest: ConversationMember;
  latest_date: string;
  message_count: number;
  unread_count: number;
  flagged: boolean;
  participants: string[];
  has_inbox: boolean;
}

export interface ConversationPage {
  scope: "inbox" | "all";
  page: number;
  page_size: number;
  total: number;
  has_next: boolean;
  results: ConversationSummary[];
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMember[];
}

export interface AttachmentInfo {
  part_id: string;
  filename: string;
  content_type: string;
  size: number;
  inline: boolean;
  content_id: string;
  previewable: boolean;
}

export interface ComposeAttachmentRef {
  folder: string;
  uid: number;
  uid_validity: number;
  part_id: string;
  filename: string;
  content_type: string;
  size: number;
}

export interface MessageDetail {
  uid: number;
  uid_validity: number;
  folder: string;
  subject: string;
  from: { name: string; address: string };
  to: string[];
  cc: string[];
  bcc?: string[];
  signature_id?: string | null;
  signature_missing?: boolean;
  quoted_text?: string; // own Drafts only; clean editor + optional quote
  reply_to: string;
  date: string;
  message_id: string;
  labels?: MessageLabel[];
  in_reply_to: string;
  references: string[];
  text: string;
  html: string;
  remote_images_blocked: boolean;
  attachments: AttachmentInfo[];
}

export interface Identity {
  address: string;
  name?: string;
  is_primary: boolean;
  kind: string;
}

/** What a signature is. Stated, not inferred from which field is filled. */
export type SignatureKind = "text" | "html" | "image";

export interface Signature {
  id: string;
  name: string;
  kind: SignatureKind;
  /** HTML mode only. Sanitised server-side; this is what came back. */
  html: string;
  /** The plain-text alternative. Derived from `html` for HTML signatures. */
  text: string;
  image_alt: string;
  image_content_type: string;
  has_image: boolean;
  /** Relative URL, so it follows whichever host PostBox is served on. */
  image_url: string;
  use_for_new: boolean;
  use_for_replies: boolean;
}

export interface Contact {
  id: string;
  name: string;
  email: string;
  company: string;
  title: string;
  phone: string;
  notes: string;
}

export type MailRuleField =
  | "from" | "to" | "subject" | "sender_domain" | "mailing_list"
  | "body" | "message_size" | "has_attachment" | "attachment_name";

export type MailRuleMatch =
  | "contains" | "is" | "not_contains" | "not_is" | "over" | "under";

export type MailRuleActionType =
  | "move" | "copy" | "archive" | "star" | "mark_read" | "mark_unread" | "delete";

export interface MailRuleCondition {
  field: MailRuleField;
  match: MailRuleMatch;
  value: string;
}

export interface MailRuleAction {
  action: MailRuleActionType;
  folder?: string;
}

export interface MailRule {
  id: string;
  name: string;
  position: number;
  enabled: boolean;

  // Primary mirrors retained for backwards-compatible clients.
  field: MailRuleField;
  match: MailRuleMatch;
  value: string;
  action: MailRuleActionType;
  action_folder: string;

  condition_mode: "all" | "any";
  conditions: MailRuleCondition[];
  actions: MailRuleAction[];
  stop_processing: boolean;
}

export interface Vacation {
  enabled: boolean;
  subject: string;
  message: string;
  starts_at: string | null;
  ends_at: string | null;
  repeat_days: number;
}

export interface SessionRow {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip_address: string | null;
  user_agent: string;
  remembered: boolean;
  current: boolean;
}

export interface AccountInfo {
  email: string;
  full_name: string;
  organization: string;
  storage: {
    used_mb: number | null;
    quota_mb: number | null;
    percent: number | null;
    available: boolean;
  };
  identities: Identity[];
  connection: {
    imap: { host: string; port: number; security: string };
    smtp: { host: string; port: number; security: string };
    username: string;
    note: string;
  };
}

export interface ScheduledRow {
  id: string;
  subject: string;
  recipients: string;
  folder: string;
  uid: number;
  uid_validity: number;
  scheduled_at: string;
  state: string;
  attempts: number;
  last_error: string;
  sent_at: string | null;
}

export interface ComposePayload {
  from_address: string;
  to: string[];
  cc?: string[];
  bcc?: string[];
  subject?: string;
  text?: string;
  html?: string;
  quoted_text?: string;
  in_reply_to?: string;
  references?: string[];
  signature_id?: string | null;
  attachments?: Array<{ filename: string; content_type: string; data: string }>;
  existing_attachments?: Array<{
    folder: string;
    uid: number;
    uid_validity: number;
    part_id: string;
  }>;
  send_at?: string | null;
  draft_uid?: number | null;
}

export type MessageAction =
  | "read" | "unread" | "star" | "unstar" | "archive"
  | "trash" | "spam" | "not-spam" | "move" | "restore" | "delete";

// ── the client ──────────────────────────────────────────────────────────────

export const postbox = {
  // auth
  login: (email: string, password: string, remember: boolean) =>
    request<{ mailbox: MailboxProfile; session: { expires_at: string } }>(
      "/auth/login/",
      { method: "POST", body: JSON.stringify({ email, password, remember }) },
    ),
  logout: () => request<{ detail: string }>("/auth/logout/", { method: "POST" }),
  logoutDevice: () =>
    request<{ detail: string }>("/auth/logout-device/", { method: "POST" }),
  logoutAll: () => request<{ detail: string }>("/auth/logout-all/", { method: "POST" }),
  accounts: () =>
    request<{ results: SavedPostBoxAccount[]; current_session_id: string | null }>(
      "/auth/accounts/",
    ),
  switchAccount: (sessionId: string) =>
    request<{ mailbox: MailboxProfile; session: { id: string; expires_at: string; remembered: boolean } }>(
      "/auth/switch/",
      { method: "POST", body: JSON.stringify({ session_id: sessionId }) },
    ),
  me: () =>
    request<{
      mailbox: MailboxProfile;
      preferences: Preferences;
      mail_client: MailClientSettings;
    }>("/auth/me/"),

  // folders
  folders: () => request<{ results: Folder[] }>("/folders/"),
  createFolder: (name: string, color?: string) =>
    request<{ name: string; color: string }>("/folders/", {
      method: "POST",
      body: JSON.stringify({ name, ...(color ? { color } : {}) }),
    }),
  renameFolder: (name: string, next: string, color?: string) =>
    request<{ name: string; color: string }>(`/folders/${encodeFolder(name)}/`, {
      method: "PATCH",
      body: JSON.stringify({ name: next, ...(color ? { color } : {}) }),
    }),
  colorFolder: (name: string, color: string) =>
    request<{ name: string; color: string }>(`/folders/${encodeFolder(name)}/`, {
      method: "PATCH", body: JSON.stringify({ color }),
    }),
  folderDeleteCheck: (name: string) =>
    request<{ name: string; message_count: number; active_rule_count: number; can_delete: boolean }>(
      "/folder-delete-check/",
      { method: "POST", body: JSON.stringify({ name }) },
    ),
  deleteFolder: (name: string) =>
    request<void>(`/folders/${encodeFolder(name)}/`, { method: "DELETE" }),

  // Virtual labels are PostBox metadata, not additional IMAP copies.
  labels: () => request<{ results: MailLabel[] }>("/labels/"),
  createLabel: (name: string, color?: string) =>
    request<MailLabel>("/labels/", {
      method: "POST", body: JSON.stringify({ name, ...(color ? { color } : {}) }),
    }),
  renameLabel: (id: string, name: string, color?: string) =>
    request<MailLabel>(`/labels/${id}/`, {
      method: "PATCH", body: JSON.stringify({ name, ...(color ? { color } : {}) }),
    }),
  colorLabel: (id: string, color: string) =>
    request<MailLabel>(`/labels/${id}/`, {
      method: "PATCH", body: JSON.stringify({ color }),
    }),
  deleteLabel: (id: string) =>
    request<void>(`/labels/${id}/`, { method: "DELETE" }),
  assignLabel: (body: {
    label_id: string; folder: string; uids: number[];
    uid_validity: number; remove?: boolean;
  }) => request<{ label: MailLabel; count: number }>("/labels/assign/", {
    method: "POST", body: JSON.stringify(body),
  }),
  labeledMessages: (id: string, params: Record<string, string | number | boolean | undefined>) =>
    request<MessagePage>(`/labels/${id}/messages/${qs(params)}`),

  // Phase 2 conversation engine; opt-in UI integration comes in Phase 3.
  conversations: (params: {
    scope?: "inbox" | "all"; page?: number; page_size?: number;
  } = {}) => request<ConversationPage>(`/conversations/${qs(params)}`),
  conversationForMessage: (folder: string, uid: number, uidValidity: number) =>
    request<ConversationDetail>(`/conversations/for-message/${qs({
      folder, uid, uid_validity: uidValidity,
    })}`),

  // messages
  messages: (params: Record<string, string | number | boolean | undefined>) =>
    request<MessagePage>(`/messages/${qs(params)}`),
  message: (folder: string, uid: number, remoteImages = false, uidValidity?: number) =>
    request<MessageDetail>(
      `/messages/${encodeFolder(folder)}/${uid}/${qs({
        remote_images: remoteImages ? "true" : undefined,
        uid_validity: uidValidity,
      })}`,
    ),
  trustRemoteImages: (folder: string, uid: number, uidValidity: number) =>
    request<{ sender: string; trusted: boolean }>(
      `/messages/${encodeFolder(folder)}/${uid}/remote-images/trust/${qs({
        uid_validity: uidValidity,
      })}`,
      { method: "POST", body: "{}" },
    ),
  trustedRemoteImageSenders: () =>
    request<{ results: Array<{ sender: string; created_at: string }> }>(
      "/remote-images/trusted-senders/",
    ),
  removeTrustedRemoteImageSender: (sender: string) =>
    request<void>("/remote-images/trusted-senders/", {
      method: "DELETE",
      body: JSON.stringify({ sender }),
    }),
  headers: (folder: string, uid: number, uidValidity: number) =>
    request<{ headers: string }>(
      `/messages/${encodeFolder(folder)}/${uid}/headers/${qs({ uid_validity: uidValidity })}`,
    ),
  rawUrl: (folder: string, uid: number, uidValidity?: number) =>
    `${BASE}/messages/${encodeFolder(folder)}/${uid}/raw/${qs({ uid_validity: uidValidity })}`,
  attachmentUrl: (folder: string, uid: number, partId: string, uidValidity?: number) =>
    `${BASE}/messages/${encodeFolder(folder)}/${uid}/attachments/${encodeURIComponent(partId)}/${qs({ uid_validity: uidValidity })}`,
  attachmentPreviewUrl: (folder: string, uid: number, partId: string, uidValidity?: number) =>
    `${BASE}/messages/${encodeFolder(folder)}/${uid}/attachments/${encodeURIComponent(partId)}/preview/${qs({ uid_validity: uidValidity })}`,
  replyContext: (folder: string, uid: number, mode: string, uidValidity?: number) =>
    request<{
      mode: string; subject: string; to: string[]; cc: string[];
      from_address: string; text: string; html: string; quoted_text: string;
      in_reply_to: string; references: string[];
      attachments: ComposeAttachmentRef[];
    }>(`/messages/${encodeFolder(folder)}/${uid}/reply-context/${qs({ mode, uid_validity: uidValidity })}`),

  act: async (
    action: MessageAction,
    folder: string,
    uids: number[],
    extra: Record<string, unknown> = {},
  ) => {
    const result = await request<{ action: string; count: number }>(
      `/messages/action/${action}/`,
      {
        method: "POST",
        body: JSON.stringify({ folder, uids, ...extra }),
      },
    );
    // Backend success is the point at which sibling tabs may update. The
    // authoritative server SSE event follows as reconciliation, but this local
    // wake-up makes the UI react without waiting for that round trip.
    announcePostBoxMailboxChange({
      kind: "mailbox_changed",
      folder,
      action,
    });
    return result;
  },

  // composing
  send: (payload: ComposePayload) =>
    request<{ sent?: boolean; scheduled?: boolean; message_id?: string; id?: string }>(
      "/compose/send/",
      { method: "POST", body: JSON.stringify(payload) },
    ),
  saveDraft: (payload: ComposePayload) =>
    request<{
      folder: string;
      uid: number;
      uid_validity: number;
      saved_at: string;
      attachments: ComposeAttachmentRef[];
    }>("/drafts/", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteDraft: (uid: number) =>
    request<void>(`/drafts/${uid}/`, { method: "DELETE" }),

  scheduled: () => request<{ results: ScheduledRow[] }>("/scheduled/"),
  rescheduleMessage: (id: string, scheduledAt: string) =>
    request<{ id: string }>(`/scheduled/${id}/`, {
      method: "PATCH",
      body: JSON.stringify({ scheduled_at: scheduledAt }),
    }),
  cancelScheduled: (id: string) =>
    request<{ id: string; state: string }>(`/scheduled/${id}/`, { method: "DELETE" }),

  // settings
  preferences: () => request<Preferences>("/preferences/"),
  savePreferences: (patch: Partial<Preferences>) =>
    request<Preferences>("/preferences/", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  account: () => request<AccountInfo>("/account/"),
  identities: () => request<{ results: Identity[] }>("/identities/"),
  forwarding: () =>
    request<{ manageable_here: boolean; detail: string; results: Array<{
      destination: string; keep_copy: boolean; status: string;
    }> }>("/forwarding/"),

  signatures: () => request<{ results: Signature[] }>("/signatures/"),
  createSignature: (body: Partial<Signature>) =>
    request<Signature>("/signatures/", { method: "POST", body: JSON.stringify(body) }),
  updateSignature: (id: string, body: Partial<Signature>) =>
    request<Signature>(`/signatures/${id}/`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteSignature: (id: string) =>
    request<void>(`/signatures/${id}/`, { method: "DELETE" }),

  /**
   * Upload an image signature.
   *
   * Not through `request`: that sets `Content-Type: application/json`, and a
   * multipart body must be allowed to set its own — the boundary is part of
   * the header and the browser generates it. Setting it by hand produces a
   * body the server cannot parse.
   */
  uploadSignatureImage: async (id: string, file: File): Promise<Signature> => {
    const form = new FormData();
    form.append("image", file);
    const response = await fetch(`${BASE}/signatures/${id}/image/`, {
      method: "POST",
      credentials: "same-origin",
      body: form,
    });
    const text = await response.text();
    let body: unknown = {};
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = { detail: text };
      }
    }
    if (!response.ok) {
      const parsed = body as Record<string, unknown>;
      const detail =
        typeof parsed.detail === "string"
          ? parsed.detail
          : firstFieldError(parsed) ?? "That image could not be uploaded.";
      throw new PostBoxError(response.status, detail);
    }
    return body as Signature;
  },

  deleteSignatureImage: (id: string) =>
    request<void>(`/signatures/${id}/image/`, { method: "DELETE" }),

  contacts: (q?: string) => request<{ results: Contact[] }>(`/contacts/${qs({ q })}`),
  createContact: (body: Partial<Contact>) =>
    request<Contact>("/contacts/", { method: "POST", body: JSON.stringify(body) }),
  updateContact: (id: string, body: Partial<Contact>) =>
    request<Contact>(`/contacts/${id}/`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteContact: (id: string) =>
    request<void>(`/contacts/${id}/`, { method: "DELETE" }),
  suggest: (q: string) =>
    request<{ results: Array<{ name: string; email: string; source: string }> }>(
      `/contacts/suggest/${qs({ q })}`,
    ),

  rules: () => request<{ results: MailRule[] }>("/rules/"),
  createRule: (body: Partial<MailRule>) =>
    request<MailRule>("/rules/", { method: "POST", body: JSON.stringify(body) }),
  updateRule: (id: string, body: Partial<MailRule>) =>
    request<MailRule>(`/rules/${id}/`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRule: (id: string) => request<void>(`/rules/${id}/`, { method: "DELETE" }),

  vacation: () => request<Vacation>("/vacation/"),
  saveVacation: (body: Partial<Vacation>) =>
    request<Vacation>("/vacation/", { method: "PATCH", body: JSON.stringify(body) }),

  // security
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ detail: string; other_sessions_revoked: number }>(
      "/security/change-password/",
      {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      },
    ),
  sessions: () => request<{ results: SessionRow[] }>("/security/sessions/"),
  revokeSession: (id: string) =>
    request<{ detail: string }>(`/security/sessions/${id}/`, { method: "DELETE" }),
};

/** Read a File as base64 for the compose payload. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result);
      // strip the `data:...;base64,` prefix
      resolve(result.slice(result.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatMessageDate(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) {
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  if (date.getFullYear() === now.getFullYear()) {
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  return date.toLocaleDateString(undefined, {
    year: "numeric", month: "short", day: "numeric",
  });
}
