"use client";

/**
 * PostBox settings.
 *
 * Every section here reflects something MateMail can actually do. Where it
 * cannot — forwarding, which is an organization-administered setting — the
 * page says so plainly instead of showing a control that would be refused.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy, Loader2, Plus, Trash2 } from "lucide-react";

import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";
import {
  postbox,
  type AccountInfo,
  type MailClientSettings,
  type MailRule,
  type SessionRow,
  type Signature,
  type SignatureKind,
  type Vacation,
} from "@/lib/postbox-api";

const SECTIONS = [
  ["general", "General"],
  ["account", "Mailbox & account"],
  ["clients", "Mail client setup"],
  ["signatures", "Signatures"],
  ["rules", "Filters & rules"],
  ["vacation", "Auto reply"],
  ["forwarding", "Forwarding & aliases"],
  ["security", "Security"],
] as const;

type Section = (typeof SECTIONS)[number][0];

export default function SettingsPage() {
  const [section, setSection] = useState<Section>("general");

  return (
    <div className="pb-scroll h-full">
      <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
        <h1 className="mb-4 text-lg font-semibold">Settings</h1>

        <div className="flex flex-col gap-5 md:flex-row">
          <nav
            className="pb-panel shrink-0 p-1 md:w-52"
            aria-label="Settings sections"
          >
            {SECTIONS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                className="pb-nav"
                aria-current={section === key ? "true" : undefined}
                onClick={() => setSection(key)}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="min-w-0 flex-1">
            {section === "general" && <GeneralSection />}
            {section === "account" && <AccountSection />}
            {section === "clients" && <ClientsSection />}
            {section === "signatures" && <SignaturesSection />}
            {section === "rules" && <RulesSection />}
            {section === "vacation" && <VacationSection />}
            {section === "forwarding" && <ForwardingSection />}
            {section === "security" && <SecuritySection />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Panel({ title, description, children }: {
  title: string; description?: string; children: React.ReactNode;
}) {
  return (
    <section className="pb-panel mb-4 p-4">
      <h2 className="text-sm font-semibold">{title}</h2>
      {description && <p className="mt-1 text-xs pb-muted">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Row({ label, htmlFor, children }: {
  label: string; htmlFor?: string; children: React.ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
      <label htmlFor={htmlFor} className="pb-label sm:w-44 sm:shrink-0">
        {label}
      </label>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

// ── general ─────────────────────────────────────────────────────────────────

function GeneralSection() {
  const { preferences, updatePreferences } = usePostBox();
  const [error, setError] = useState<string | null>(null);

  const set = useCallback(
    async (patch: Parameters<typeof updatePreferences>[0]) => {
      setError(null);
      try {
        await updatePreferences(patch);
      } catch (caught) {
        setError(describePostBoxError(caught, "That preference could not be saved."));
      }
    },
    [updatePreferences],
  );

  return (
    <Panel
      title="Appearance and reading"
      description="These follow you between devices."
    >
      <Row label="Theme" htmlFor="pb-theme">
        <select id="pb-theme" className="pb-select" value={preferences.theme}
          onChange={(e) => void set({ theme: e.target.value as "light" })}>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
          <option value="system">Match my device</option>
        </select>
      </Row>
      <Row label="Density" htmlFor="pb-density">
        <select id="pb-density" className="pb-select" value={preferences.density}
          onChange={(e) => void set({ density: e.target.value as "compact" })}>
          <option value="comfortable">Comfortable</option>
          <option value="compact">Compact</option>
        </select>
      </Row>
      <Row label="Reading pane" htmlFor="pb-pane">
        <select id="pb-pane" className="pb-select" value={preferences.reading_pane}
          onChange={(e) => void set({ reading_pane: e.target.value as "right" })}>
          <option value="right">To the right</option>
          <option value="bottom">Below the list</option>
          <option value="off">Off</option>
        </select>
      </Row>
      <Row label="Messages per page" htmlFor="pb-per-page">
        <input id="pb-per-page" className="pb-input pb-num" type="number" min={10} max={100}
          value={preferences.messages_per_page}
          onChange={(e) => void set({ messages_per_page: Number(e.target.value) })} />
      </Row>
      <Row label="Time zone" htmlFor="pb-tz">
        <input id="pb-tz" className="pb-input" value={preferences.timezone_name}
          onChange={(e) => void set({ timezone_name: e.target.value })} />
        <p className="mt-1 text-xs pb-subtle">
          Used for dates and for scheduled send. An IANA name, such as
          Asia/Dhaka.
        </p>
      </Row>

      <label className="mb-2 flex items-center gap-2 text-xs">
        <input type="checkbox" checked={preferences.load_remote_images}
          onChange={(e) => void set({ load_remote_images: e.target.checked })} />
        Always load remote images
      </label>
      <p className="mb-3 text-xs pb-subtle">
        Off by default. A remote image tells the sender you opened the message
        and reveals your IP address.
      </p>

      <label className="mb-2 flex items-center gap-2 text-xs">
        <input type="checkbox" checked={preferences.notify_in_app}
          onChange={(e) => void set({ notify_in_app: e.target.checked })} />
        Show notifications while PostBox is open
      </label>
      <p className="text-xs pb-subtle">
        MateMail does not run a push service, so notifications only appear while
        this tab is open.
      </p>

      {error && (
        <p className="mt-3 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
          {error}
        </p>
      )}
    </Panel>
  );
}

// ── account ─────────────────────────────────────────────────────────────────

/** One value with a copy button — these get typed into another app. */
function CopyValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <span className="inline-flex items-center gap-2">
      <code className="text-xs">{value}</code>
      <button
        type="button"
        className="pb-btn pb-btn-plain"
        aria-label={`Copy ${value}`}
        onClick={() => {
          // Clipboard access can be refused (insecure context, denied
          // permission). The value is on screen either way, so a failure
          // just means no tick — never an error the reader must dismiss.
          navigator.clipboard?.writeText(value).then(
            () => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            },
            () => undefined,
          );
        }}
      >
        {copied ? (
          <Check className="h-3.5 w-3.5" aria-hidden="true" />
        ) : (
          <Copy className="h-3.5 w-3.5" aria-hidden="true" />
        )}
      </button>
    </span>
  );
}

/**
 * Mail client setup.
 *
 * The servers come from the API, not from literals here — they are a
 * property of the deployment, and a page that printed a stale hostname
 * would send people to configure a client that cannot connect.
 */
function ClientsSection() {
  const [client, setClient] = useState<MailClientSettings | null>(null);

  useEffect(() => {
    postbox.me().then((data) => setClient(data.mail_client)).catch(() => setClient(null));
  }, []);

  if (!client) {
    return (
      <Panel title="Mail client setup">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      </Panel>
    );
  }

  return (
    <>
      <Panel
        title="Incoming mail (IMAP)"
        description="Your mail stays on the server, so every device sees the same mailbox."
      >
        <Row label="Server"><CopyValue value={client.imap.server} /></Row>
        <Row label="Port"><CopyValue value={String(client.imap.port)} /></Row>
        <Row label="Encryption">{client.imap.encryption}</Row>
        <Row label="Username"><CopyValue value={client.username} /></Row>
        <Row label="Password">Your mailbox password</Row>
      </Panel>

      <Panel
        title="Outgoing mail (SMTP)"
        description="Authentication is required. A client set to send without signing in will be refused."
      >
        <Row label="Server"><CopyValue value={client.smtp.server} /></Row>
        <Row label="Port"><CopyValue value={String(client.smtp.port)} /></Row>
        <Row label="Encryption">{client.smtp.encryption}</Row>
        <Row label="Username"><CopyValue value={client.username} /></Row>
        <Row label="Password">Your mailbox password</Row>
      </Panel>

      <Panel title="Outlook">
        <p className="text-xs pb-muted">
          MateMail works with Outlook over IMAP and authenticated SMTP using the
          settings above. Some Outlook versions will not configure an IMAP
          account on their own and need Advanced or manual setup — choose IMAP
          rather than letting Outlook pick, then enter the servers and ports
          exactly as shown.
        </p>
        <p className="mt-2 text-xs pb-muted">
          Your username is the whole address, not the part before the @.
        </p>
      </Panel>

      <Panel title="What is not offered">
        <p className="text-xs pb-muted">
          POP3 and port 465 are not available, and plain IMAP on 143 is not
          served at all — a password must never cross a network unencrypted. A
          client configured for any of those will not connect; that is the
          setting being wrong rather than the server being down.
        </p>
      </Panel>
    </>
  );
}

function AccountSection() {
  const [account, setAccount] = useState<AccountInfo | null>(null);

  useEffect(() => {
    postbox.account().then(setAccount).catch(() => setAccount(null));
  }, []);

  if (!account) {
    return (
      <Panel title="Mailbox & account">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      </Panel>
    );
  }

  const { storage } = account;

  return (
    <>
      <Panel title="Mailbox">
        <Row label="Address">{account.email}</Row>
        <Row label="Display name">{account.full_name || "—"}</Row>
        <Row label="Organization">{account.organization || "—"}</Row>
      </Panel>

      <Panel title="Storage">
        {storage.available && storage.quota_mb ? (
          <>
            <div className="mb-2 h-2 w-full" style={{ background: "var(--pb-surface-2)" }}>
              <div
                className="h-2"
                style={{
                  width: `${Math.min(storage.percent ?? 0, 100)}%`,
                  background:
                    (storage.percent ?? 0) > 90
                      ? "var(--pb-danger)"
                      : "var(--pb-primary)",
                }}
              />
            </div>
            <p className="text-xs pb-muted pb-num">
              {formatMb(storage.used_mb)} of {formatMb(storage.quota_mb)} used
              {storage.percent !== null ? ` (${storage.percent}%)` : ""}
            </p>
            {(storage.percent ?? 0) > 90 && (
              <p className="mt-1 text-xs" style={{ color: "var(--pb-danger)" }}>
                Your mailbox is nearly full. Delete or archive some mail.
              </p>
            )}
          </>
        ) : (
          // "Unknown" and "empty" are different facts. A bar at 0% for an
          // unknown value would be a fabrication.
          <p className="text-xs pb-muted">
            Storage usage is not available right now.
          </p>
        )}
      </Panel>

      <Panel
        title="Sending identities"
        description="Addresses you may send from. Managed by your organization."
      >
        <ul className="space-y-1 text-sm">
          {account.identities.map((identity) => (
            <li key={identity.address} className="flex items-center gap-2">
              <span>{identity.address}</span>
              {identity.is_primary && <span className="pb-chip">Primary</span>}
              {identity.kind === "alias" && <span className="pb-chip">Alias</span>}
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Connection settings"
        description="For a desktop or phone mail app."
      >
        <Row label="Incoming (IMAP)">
          {account.connection.imap.host}:{account.connection.imap.port} ·{" "}
          {account.connection.imap.security}
        </Row>
        <Row label="Outgoing (SMTP)">
          {account.connection.smtp.host}:{account.connection.smtp.port} ·{" "}
          {account.connection.smtp.security}
        </Row>
        <Row label="Username">{account.connection.username}</Row>
        <p className="text-xs pb-subtle">{account.connection.note}</p>
      </Panel>
    </>
  );
}

function formatMb(value: number | null): string {
  if (value === null) return "—";
  if (value < 1024) return `${value} MB`;
  return `${(value / 1024).toFixed(1)} GB`;
}

// ── signatures ──────────────────────────────────────────────────────────────

/**
 * Signatures.
 *
 * Three explicit modes rather than one content box. The previous version had a
 * single textarea bound to `signature.text` under a label mentioning HTML — so
 * somebody pasted a designed HTML signature, it was stored as plain text, and
 * the recipient read the markup. The type is now something the person chooses
 * and can see, and each mode gets the editor it actually needs.
 *
 * Nothing here autosaves. An HTML or image signature is edited deliberately and
 * saved deliberately: a blur-save would push half-finished markup to the server
 * on every focus change, and the server sanitises on write, so a half-saved
 * value is a silently altered one.
 */
function SignaturesSection() {
  const [items, setItems] = useState<Signature[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    postbox
      .signatures()
      .then((d) => setItems(d.results))
      .catch(() => setItems([]));
  }, []);

  useEffect(load, [load]);

  const setDefault = async (
    signatureId: string,
    patch: Pick<Signature, "use_for_new"> | Pick<Signature, "use_for_replies">,
  ) => {
    setError(null);
    try {
      await postbox.updateSignature(signatureId, patch);
      load();
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature default could not be saved."));
    }
  };

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await postbox.createSignature({
        name: "New signature",
        kind: "text",
        text: "",
      });
      load();
      // Straight into the editor: a new signature with no content is not
      // something anyone wants to look at in a list.
      setEditingId(created.id);
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature could not be created."));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (signature: Signature) => {
    setError(null);
    try {
      await postbox.deleteSignature(signature.id);
      if (editingId === signature.id) setEditingId(null);
      load();
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature could not be removed."));
    }
  };

  return (
    <Panel
      title="Signatures"
      description="Choose one in the composer, or set a default below. HTML is cleaned when saved."
    >
      {items.length === 0 && (
        <p className="mb-3 text-xs pb-muted">
          No signatures yet.
        </p>
      )}

      {items.map((signature) =>
        editingId === signature.id ? (
          <SignatureEditor
            key={signature.id}
            signature={signature}
            onClose={() => setEditingId(null)}
            onSaved={() => {
              setEditingId(null);
              load();
            }}
          />
        ) : (
          <SignatureRow
            key={signature.id}
            signature={signature}
            onEdit={() => setEditingId(signature.id)}
            onDelete={() => void remove(signature)}
            onDefault={setDefault}
          />
        ),
      )}

      {error && (
        <p className="mb-2 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
          {error}
        </p>
      )}

      <button type="button" className="pb-btn pb-btn-ghost" disabled={busy} onClick={add}>
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add signature
      </button>
    </Panel>
  );
}

const KIND_LABEL: Record<SignatureKind, string> = {
  text: "Text",
  html: "HTML",
  image: "Image",
};

function SignatureRow({
  signature,
  onEdit,
  onDelete,
  onDefault,
}: {
  signature: Signature;
  onEdit: () => void;
  onDelete: () => void;
  onDefault: (
    id: string,
    patch: Pick<Signature, "use_for_new"> | Pick<Signature, "use_for_replies">,
  ) => void;
}) {
  return (
    <div className="mb-3 border p-3" style={{ borderColor: "var(--pb-border)" }}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{signature.name}</span>
        <span
          className="px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
          style={{ background: "var(--pb-surface-2)", color: "var(--pb-subtle)" }}
        >
          {KIND_LABEL[signature.kind]}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className="pb-btn pb-btn-plain" onClick={onEdit}>
            Edit
          </button>
          <button
            type="button"
            className="pb-btn pb-btn-plain"
            aria-label={`Delete ${signature.name}`}
            onClick={onDelete}
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>

      <SignaturePreview signature={signature} />

      <div className="mt-2 flex flex-wrap gap-4 text-xs">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={signature.use_for_new}
            onChange={(e) => onDefault(signature.id, { use_for_new: e.target.checked })}
          />
          Default for new messages
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={signature.use_for_replies}
            onChange={(e) =>
              onDefault(signature.id, { use_for_replies: e.target.checked })
            }
          />
          Default for replies and forwards
        </label>
      </div>
    </div>
  );
}

/**
 * How a signature will look.
 *
 * `dangerouslySetInnerHTML` is used for the HTML mode, and the value is the
 * SERVER'S sanitised response — never the text in the editor. That distinction
 * is the whole point: a preview rendered from unsaved input would show
 * something the recipient will never receive, and would make the browser the
 * authority on what is safe.
 */
function SignaturePreview({ signature }: { signature: Signature }) {
  if (signature.kind === "image") {
    return signature.has_image ? (
      // A signature image is arbitrary user content served from our own API;
      // next/image would try to optimise and re-host it.
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={signature.image_url}
        alt={signature.image_alt}
        style={{ maxWidth: "100%", maxHeight: "120px" }}
      />
    ) : (
      <p className="text-xs pb-muted">No image uploaded yet.</p>
    );
  }

  if (signature.kind === "html") {
    return (
      <div
        className="text-sm"
        // Sanitised server-side on write; this is what came back.
        dangerouslySetInnerHTML={{ __html: signature.html }}
      />
    );
  }

  return (
    <pre className="text-xs pb-muted" style={{ whiteSpace: "pre-wrap", margin: 0 }}>
      {signature.text || "(empty)"}
    </pre>
  );
}

/** The editor. One form, one explicit Save. */
function SignatureEditor({
  signature,
  onClose,
  onSaved,
}: {
  signature: Signature;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(signature.name);
  const [kind, setKind] = useState<SignatureKind>(signature.kind);
  const [text, setText] = useState(signature.text);
  const [html, setHtml] = useState(signature.html);
  const [alt, setAlt] = useState(signature.image_alt);
  const [preview, setPreview] = useState<Signature | null>(signature);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const patch: Partial<Signature> = { name, kind };
      if (kind === "html") patch.html = html;
      if (kind === "text") patch.text = text;
      if (kind === "image") patch.image_alt = alt;

      const saved = await postbox.updateSignature(signature.id, patch);
      // Echo the SERVER's version back into the editor, so what is shown is
      // what was stored — including anything the sanitiser removed.
      setPreview(saved);
      setHtml(saved.html);
      setText(saved.text);
      onSaved();
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const saved = await postbox.uploadSignatureImage(signature.id, file);
      setPreview(saved);
    } catch (caught) {
      setError(describePostBoxError(caught, "That image could not be uploaded."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-3 border p-3" style={{ borderColor: "var(--pb-accent)" }}>
      <Row label="Name" htmlFor={`sig-name-${signature.id}`}>
        <input
          id={`sig-name-${signature.id}`}
          className="pb-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Row>

      <Row label="Type" htmlFor={`sig-kind-${signature.id}`}>
        <select
          id={`sig-kind-${signature.id}`}
          className="pb-input"
          value={kind}
          onChange={(e) => setKind(e.target.value as SignatureKind)}
        >
          <option value="text">Plain text</option>
          <option value="html">HTML</option>
          <option value="image">Image</option>
        </select>
      </Row>

      {kind === "text" && (
        <>
          <Row label="Signature text" htmlFor={`sig-text-${signature.id}`}>
            <textarea
              id={`sig-text-${signature.id}`}
              className="pb-textarea"
              rows={5}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </Row>
          <p className="mb-3 text-xs pb-muted">
            Plain text. Anything that looks like markup is sent as those
            characters — choose HTML above if you want it rendered.
          </p>
        </>
      )}

      {kind === "html" && (
        <>
          <Row label="HTML source" htmlFor={`sig-html-${signature.id}`}>
            <textarea
              id={`sig-html-${signature.id}`}
              className="pb-textarea"
              rows={10}
              spellCheck={false}
              style={{ fontFamily: "ui-monospace, monospace", fontSize: "12px" }}
              value={html}
              onChange={(e) => setHtml(e.target.value)}
            />
          </Row>
          <p className="mb-3 text-xs pb-muted">
            Paste a complete signature — a whole HTML document is fine, the
            wrapper is removed. Scripts, embedded frames and layout CSS that
            could cover the page are stripped on save. Style blocks do not
            survive: use inline <code>style=</code> attributes, which is what
            mail clients support. Web fonts will not load in most clients.
          </p>
        </>
      )}

      {kind === "image" && (
        <>
          <Row label="Image" htmlFor={`sig-file-${signature.id}`}>
            <input
              id={`sig-file-${signature.id}`}
              ref={fileInput}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              className="text-xs"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void upload(file);
              }}
            />
          </Row>
          <Row label="Alt text" htmlFor={`sig-alt-${signature.id}`}>
            <input
              id={`sig-alt-${signature.id}`}
              className="pb-input"
              value={alt}
              placeholder="NetaMate Solutions logo"
              onChange={(e) => setAlt(e.target.value)}
            />
          </Row>
          <p className="mb-3 text-xs pb-muted">
            PNG, JPEG, GIF or WebP, up to 256&nbsp;KB — it is sent with every
            message. Alt text is required: it is what a reader sees when images
            are blocked, which is the default in many workplaces. WebP does not
            render in Outlook for Windows; PNG is the safe choice.
          </p>
        </>
      )}

      {preview && (
        <div className="mb-3">
          <p className="pb-label mb-1">Preview</p>
          <div className="border p-2" style={{ borderColor: "var(--pb-border)" }}>
            <SignaturePreview signature={{ ...preview, kind }} />
          </div>
          <p className="mt-1 text-xs pb-subtle">
            Shows what was saved, after cleaning — not what is in the box above.
          </p>
        </div>
      )}

      {error && (
        <p className="mb-2 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
          {error}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          className="pb-btn pb-btn-primary"
          disabled={busy}
          onClick={() => void save()}
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          Save signature
        </button>
        <button type="button" className="pb-btn pb-btn-ghost" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}

// ── rules ───────────────────────────────────────────────────────────────────

function RulesSection() {
  const [rules, setRules] = useState<MailRule[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    postbox.rules().then((d) => setRules(d.results)).catch(() => setRules([]));
    postbox.folders()
      .then((d) => setFolders(d.results.map((f) => f.name)))
      .catch(() => setFolders([]));
  }, []);

  useEffect(load, [load]);

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await postbox.createRule({
        name: "New rule", field: "from", match: "contains",
        value: "example.com", action: "move",
        action_folder: folders[0] ?? "INBOX",
        position: rules.length, enabled: true,
      });
      load();
    } catch (caught) {
      setError(describePostBoxError(caught, "That rule could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Filters and rules"
      description="Applied by the mail server as messages arrive, in order, top to bottom."
    >
      {rules.length === 0 && (
        <p className="mb-3 text-xs pb-muted">No rules yet.</p>
      )}

      {rules.map((rule) => (
        <div
          key={rule.id}
          className="mb-3 border p-3"
          style={{ borderColor: "var(--pb-border)" }}
        >
          <div className="mb-2 flex items-center gap-2">
            <input className="pb-input" aria-label="Rule name" defaultValue={rule.name}
              onBlur={(e) => void postbox.updateRule(rule.id, { name: e.target.value })} />
            <label className="flex items-center gap-1.5 text-xs whitespace-nowrap">
              <input type="checkbox" defaultChecked={rule.enabled}
                onChange={(e) =>
                  void postbox.updateRule(rule.id, { enabled: e.target.checked })
                } />
              On
            </label>
            <button type="button" className="pb-btn pb-btn-plain"
              aria-label={`Delete ${rule.name}`}
              onClick={async () => {
                await postbox.deleteRule(rule.id);
                load();
              }}>
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span>If</span>
            <select className="pb-select" style={{ width: "auto" }}
              defaultValue={rule.field}
              onChange={(e) =>
                void postbox.updateRule(rule.id, { field: e.target.value as "from" })
              }>
              <option value="from">From</option>
              <option value="to">To or Cc</option>
              <option value="subject">Subject</option>
            </select>
            <select className="pb-select" style={{ width: "auto" }}
              defaultValue={rule.match}
              onChange={(e) =>
                void postbox.updateRule(rule.id, { match: e.target.value as "contains" })
              }>
              <option value="contains">contains</option>
              <option value="is">is exactly</option>
            </select>
            <input className="pb-input" style={{ width: "auto", minWidth: "10rem" }}
              aria-label="Match value" defaultValue={rule.value}
              onBlur={(e) => void postbox.updateRule(rule.id, { value: e.target.value })} />
            <span>then</span>
            <select className="pb-select" style={{ width: "auto" }}
              defaultValue={rule.action}
              onChange={(e) =>
                void postbox.updateRule(rule.id, { action: e.target.value as "move" })
              }>
              <option value="move">move to</option>
              <option value="star">star it</option>
              <option value="mark_read">mark as read</option>
              <option value="delete">move to Trash</option>
            </select>
            {rule.action === "move" && (
              <select className="pb-select" style={{ width: "auto" }}
                aria-label="Destination folder"
                defaultValue={rule.action_folder}
                onChange={(e) =>
                  void postbox.updateRule(rule.id, { action_folder: e.target.value })
                }>
                {folders.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            )}
          </div>
        </div>
      ))}

      {error && (
        <p className="mb-2 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
          {error}
        </p>
      )}

      <button type="button" className="pb-btn pb-btn-ghost" disabled={busy} onClick={add}>
        <Plus className="h-3.5 w-3.5" aria-hidden="true" />
        Add rule
      </button>
    </Panel>
  );
}

// ── vacation ────────────────────────────────────────────────────────────────

function VacationSection() {
  const [vacation, setVacation] = useState<Vacation | null>(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    postbox.vacation().then(setVacation).catch(() => setVacation(null));
  }, []);

  const save = async (patch: Partial<Vacation>) => {
    setError(null);
    setSaved(false);
    try {
      const next = await postbox.saveVacation(patch);
      setVacation(next);
      setSaved(true);
    } catch (caught) {
      setError(describePostBoxError(caught, "That could not be saved."));
    }
  };

  if (!vacation) {
    return (
      <Panel title="Automatic reply">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      </Panel>
    );
  }

  return (
    <Panel
      title="Automatic reply"
      description="Sent by the mail server, at most once per sender in the period below. It does not reply to mailing lists or bounces."
    >
      <label className="mb-3 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={vacation.enabled}
          onChange={(e) => void save({ enabled: e.target.checked })} />
        Send an automatic reply
      </label>

      <Row label="Subject" htmlFor="pb-vac-subject">
        <input id="pb-vac-subject" className="pb-input" defaultValue={vacation.subject}
          onBlur={(e) => void save({ subject: e.target.value })} />
      </Row>
      <Row label="Message" htmlFor="pb-vac-message">
        <textarea id="pb-vac-message" className="pb-textarea" rows={4}
          defaultValue={vacation.message}
          onBlur={(e) => void save({ message: e.target.value })} />
      </Row>
      <Row label="Starts" htmlFor="pb-vac-start">
        <input id="pb-vac-start" className="pb-input" type="datetime-local"
          defaultValue={toLocal(vacation.starts_at)}
          onBlur={(e) =>
            void save({ starts_at: e.target.value ? new Date(e.target.value).toISOString() : null })
          } />
      </Row>
      <Row label="Ends" htmlFor="pb-vac-end">
        <input id="pb-vac-end" className="pb-input" type="datetime-local"
          defaultValue={toLocal(vacation.ends_at)}
          onBlur={(e) =>
            void save({ ends_at: e.target.value ? new Date(e.target.value).toISOString() : null })
          } />
      </Row>
      <Row label="Reply again after" htmlFor="pb-vac-days">
        <input id="pb-vac-days" className="pb-input pb-num" type="number" min={1} max={60}
          defaultValue={vacation.repeat_days}
          onBlur={(e) => void save({ repeat_days: Number(e.target.value) })} />
      </Row>

      {saved && <p className="text-xs" style={{ color: "var(--pb-success)" }}>Saved.</p>}
      {error && (
        <p className="text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>{error}</p>
      )}
    </Panel>
  );
}

function toLocal(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

// ── forwarding ──────────────────────────────────────────────────────────────

function ForwardingSection() {
  const [data, setData] = useState<Awaited<ReturnType<typeof postbox.forwarding>> | null>(null);

  useEffect(() => {
    postbox.forwarding().then(setData).catch(() => setData(null));
  }, []);

  return (
    <Panel title="Forwarding and aliases">
      <p className="mb-3 text-xs pb-muted">
        {data?.detail ??
          "Forwarding is managed by your organization's MateMail administrator."}
      </p>
      {data && data.results.length > 0 ? (
        <ul className="space-y-1 text-sm">
          {data.results.map((rule) => (
            <li key={rule.destination} className="flex items-center gap-2">
              <span>{rule.destination}</span>
              <span className="pb-chip">{rule.status}</span>
              {rule.keep_copy && <span className="pb-chip">keeps a copy</span>}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs pb-subtle">No forwarding is set up for this mailbox.</p>
      )}
    </Panel>
  );
}

// ── security ────────────────────────────────────────────────────────────────

function SecuritySection() {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    postbox.sessions().then((d) => setSessions(d.results)).catch(() => setSessions([]));
  }, []);

  useEffect(load, [load]);

  const changePassword = async () => {
    setError(null);
    setMessage(null);
    if (next !== confirm) {
      setError("The two new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const result = await postbox.changePassword(current, next);
      setMessage(
        `${result.detail} ${result.other_sessions_revoked} other session(s) were signed out.`,
      );
      setCurrent("");
      setNext("");
      setConfirm("");
      load();
    } catch (caught) {
      setError(describePostBoxError(caught, "Your password could not be changed."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Panel
        title="Mailbox password"
        description="This is the password you use in PostBox and in any mail app."
      >
        <Row label="Current password" htmlFor="pb-current">
          <input id="pb-current" className="pb-input" type="password"
            autoComplete="current-password" value={current}
            onChange={(e) => setCurrent(e.target.value)} />
        </Row>
        <Row label="New password" htmlFor="pb-new">
          <input id="pb-new" className="pb-input" type="password"
            autoComplete="new-password" value={next}
            onChange={(e) => setNext(e.target.value)} />
        </Row>
        <Row label="Confirm new password" htmlFor="pb-confirm">
          <input id="pb-confirm" className="pb-input" type="password"
            autoComplete="new-password" value={confirm}
            onChange={(e) => setConfirm(e.target.value)} />
        </Row>

        {message && (
          <p className="mb-2 text-xs" style={{ color: "var(--pb-success)" }}>{message}</p>
        )}
        {error && (
          <p className="mb-2 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
            {error}
          </p>
        )}

        <button type="button" className="pb-btn pb-btn-primary"
          disabled={busy || !current || !next} onClick={() => void changePassword()}>
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
          Change password
        </button>
      </Panel>

      <Panel
        title="Signed-in devices"
        description="Each one is a browser that is currently signed in to PostBox."
      >
        <ul className="mb-3 space-y-2">
          {sessions.map((session) => (
            <li key={session.id} className="flex flex-wrap items-center gap-2 text-xs">
              <div className="min-w-0 flex-1">
                <p className="truncate">
                  {session.user_agent || "Unknown device"}
                  {session.current && <span className="pb-chip ml-2">This device</span>}
                </p>
                <p className="pb-subtle pb-num">
                  {session.ip_address ?? "unknown address"} · last active{" "}
                  {new Date(session.last_seen_at).toLocaleString()}
                </p>
              </div>
              {!session.current && (
                <button type="button" className="pb-btn pb-btn-ghost"
                  onClick={async () => {
                    await postbox.revokeSession(session.id);
                    load();
                  }}>
                  Sign out
                </button>
              )}
            </li>
          ))}
        </ul>

        <button type="button" className="pb-btn pb-btn-danger"
          onClick={async () => {
            await postbox.logoutAll();
            window.location.href = "/postbox/login";
          }}>
          Sign out on all devices
        </button>
      </Panel>
    </>
  );
}
