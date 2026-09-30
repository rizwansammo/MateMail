"use client";

import {
  ArrowDown,
  ArrowUp,
  AtSign,
  Bell,
  Check,
  ChevronLeft,
  Copy,
  Forward,
  HardDrive,
  ImagePlus,
  KeyRound,
  ListFilter,
  Loader2,
  Mail,
  Monitor,
  Palette,
  Pencil,
  PenLine,
  Plane,
  Plus,
  Shield,
  Smartphone,
  Trash2,
  User,
  X,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { useAsyncData } from "@/components/postbox/use-async";
import { describePostBoxError, usePostBox } from "@/contexts/postbox-context";
import {
  postbox,
  type AccountInfo,
  type Folder,
  type Identity,
  type MailClientSettings,
  type MailRule,
  type SessionRow,
  type Signature,
  type SignatureKind,
  type Vacation,
} from "@/lib/postbox-api";

const AREAS = [
  ["appearance", "Appearance & Layout", Palette],
  ["mail", "Mail Preferences", Mail],
  ["account", "Account", User],
  ["identities", "Identities", AtSign],
  ["signatures", "Signatures", PenLine],
  ["rules", "Rules", ListFilter],
  ["vacation", "Vacation", Plane],
  ["forwarding", "Forwarding", Forward],
  ["notifications", "Notifications", Bell],
  ["security", "Security", Shield],
  ["clients", "Mail Client Setup", Monitor],
] as const;

type Area = (typeof AREAS)[number][0];

const DESCRIPTIONS: Record<Area, string> = {
  appearance: "Choose how PostBox looks and how messages open.",
  mail: "Control mailbox behavior, privacy, and your default sender.",
  account: "Your mailbox identity and real storage usage.",
  identities: "Review the addresses you are allowed to send from.",
  signatures: "Create text, HTML, or image signatures for outgoing mail.",
  rules: "Organize incoming messages automatically with server-side rules.",
  vacation: "Send an automatic response while you are away.",
  forwarding: "See where incoming mail is forwarded by your organization.",
  notifications: "Choose the alerts PostBox can genuinely provide.",
  security: "Change your password and review active sessions.",
  clients: "Use the live IMAP and SMTP settings with another mail app.",
};

export default function PremiumSettings() {
  const [area, setArea] = useState<Area>("appearance");
  const current = AREAS.find(([key]) => key === area)!;
  const Icon = current[2];

  return (
    <div className="pb-premium-settings">
      <aside className="pb-premium-settings-nav">
        <Link href="/postbox" className="pb-settings-back">
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          Back to mailbox
        </Link>
        <div className="pb-settings-nav-title">Settings</div>
        <nav aria-label="Settings sections">
          {AREAS.map(([key, label, AreaIcon]) => (
            <button
              key={key}
              type="button"
              className={area === key ? "active" : ""}
              aria-current={area === key ? "page" : undefined}
              onClick={() => setArea(key)}
            >
              <AreaIcon className="h-[17px] w-[17px]" aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>
      </aside>

      <main className="pb-premium-settings-main pb-scroll">
        <header className="pb-settings-heading">
          <div className="pb-settings-heading-icon">
            <Icon className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <h1>{current[1]}</h1>
            <p>{DESCRIPTIONS[area]}</p>
          </div>
        </header>

        <div className="pb-settings-content">
          {area === "appearance" && <AppearanceSection />}
          {area === "mail" && <MailPreferencesSection />}
          {area === "account" && <AccountSection />}
          {area === "identities" && <IdentitiesSection onOpenMail={() => setArea("mail")} />}
          {area === "signatures" && <SignaturesSection />}
          {area === "rules" && <RulesSection />}
          {area === "vacation" && <VacationSection />}
          {area === "forwarding" && <ForwardingSection />}
          {area === "notifications" && <NotificationsSection />}
          {area === "security" && <SecuritySection />}
          {area === "clients" && <ClientSetupSection />}
        </div>
      </main>
    </div>
  );
}

function SettingsCard({
  title,
  description,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`pb-setting-card ${className}`}>
      {(title || description) && (
        <header>
          {title && <h2>{title}</h2>}
          {description && <p>{description}</p>}
        </header>
      )}
      {children}
    </section>
  );
}

function SettingRow({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="pb-setting-row">
      <div>
        <strong>{label}</strong>
        {description && <p>{description}</p>}
      </div>
      <div className="pb-setting-control">{children}</div>
    </div>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  disabled = false,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className={`pb-setting-toggle ${disabled ? "disabled" : ""}`}>
      <span>
        <strong>{label}</strong>
        <p>{description}</p>
      </span>
      <span className="pb-switch">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span aria-hidden="true" />
      </span>
    </label>
  );
}

function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="pb-settings-error" role="alert">{message}</p>;
}

function AppearanceSection() {
  const { preferences, updatePreferences } = usePostBox();
  const [error, setError] = useState<string | null>(null);

  const save = useCallback(
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
    <>
      <SettingsCard
        title="Inbox layout"
        description="Choose how the message list and reader share the workspace."
      >
        <div className="pb-layout-choice-grid">
          {([
            ["off", "Full message", "Open one message at a time with the most reading space."],
            ["right", "Right pane", "Keep the message list visible beside the reader."],
            ["bottom", "Bottom pane", "Keep the list above the message you are reading."],
          ] as const).map(([value, label, description]) => (
            <button
              key={value}
              type="button"
              className={preferences.reading_pane === value ? "active" : ""}
              onClick={() => void save({ reading_pane: value })}
            >
              <span className={`pb-layout-preview pb-layout-preview-${value}`} aria-hidden="true">
                <i /><i /><i />
              </span>
              <strong>{label}</strong>
              <small>{description}</small>
              {preferences.reading_pane === value && (
                <Check className="pb-layout-choice-check h-4 w-4" aria-hidden="true" />
              )}
            </button>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Appearance">
        <SettingRow label="Theme" description="Follow your device or choose a fixed appearance.">
          <select
            className="pb-settings-select"
            value={preferences.theme}
            onChange={(event) =>
              void save({ theme: event.target.value as "light" | "dark" | "system" })
            }
          >
            <option value="system">Match my device</option>
            <option value="light">Light</option>
            <option value="dark">Dark</option>
          </select>
        </SettingRow>
        <SettingRow label="Density" description="Change how much vertical space each message row uses.">
          <div className="pb-segmented">
            {(["comfortable", "compact"] as const).map((density) => (
              <button
                key={density}
                type="button"
                className={preferences.density === density ? "active" : ""}
                onClick={() => void save({ density })}
              >
                {density === "comfortable" ? "Comfortable" : "Compact"}
              </button>
            ))}
          </div>
        </SettingRow>
      </SettingsCard>
      <InlineError message={error} />
    </>
  );
}

function MailPreferencesSection() {
  const { preferences, updatePreferences } = usePostBox();
  const [error, setError] = useState<string | null>(null);
  const identities = useAsyncData(() => postbox.identities(), [], "Identities could not be loaded.");
  const trusted = useAsyncData(
    () => postbox.trustedRemoteImageSenders(),
    [],
    "Trusted image senders could not be loaded.",
  );

  const save = useCallback(
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

  const removeTrusted = async (sender: string) => {
    setError(null);
    try {
      await postbox.removeTrustedRemoteImageSender(sender);
      await trusted.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That trusted sender could not be removed."));
    }
  };

  return (
    <>
      <SettingsCard title="Message list">
        <SettingRow label="Messages per page" description="Between 10 and 100 messages per page.">
          <select
            className="pb-settings-select"
            value={preferences.messages_per_page}
            onChange={(event) => void save({ messages_per_page: Number(event.target.value) })}
          >
            {[10, 25, 50, 75, 100].map((value) => (
              <option key={value} value={value}>{value}</option>
            ))}
          </select>
        </SettingRow>
        <SettingRow
          label="Time zone"
          description="Used for message dates and scheduled send. Enter an IANA time zone."
        >
          <input
            className="pb-settings-input"
            value={preferences.timezone_name}
            onChange={(event) => void save({ timezone_name: event.target.value })}
            aria-label="Time zone"
          />
        </SettingRow>
        <SettingRow label="Default sender" description="The From address selected for a new message.">
          <select
            className="pb-settings-select"
            value={preferences.default_identity}
            disabled={identities.loading}
            onChange={(event) => void save({ default_identity: event.target.value })}
          >
            <option value="">Primary identity</option>
            {(identities.data?.results ?? []).map((identity) => (
              <option key={identity.address} value={identity.address}>
                {identity.name ? `${identity.name} — ${identity.address}` : identity.address}
              </option>
            ))}
          </select>
        </SettingRow>
      </SettingsCard>

      <SettingsCard
        title="Remote image privacy"
        description="Remote images can tell a sender when you opened a message and reveal your network address."
      >
        <ToggleRow
          label="Always load remote images"
          description="Load remote images for every sender without asking."
          checked={preferences.load_remote_images}
          onChange={(value) => void save({ load_remote_images: value })}
        />

        <div className="pb-trusted-senders">
          <div className="pb-settings-subheading">
            <strong>Always display images from these senders</strong>
            <span>{trusted.data?.results.length ?? 0}</span>
          </div>
          {trusted.loading ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : trusted.data?.results.length ? (
            <ul>
              {trusted.data.results.map((row) => (
                <li key={row.sender}>
                  <span>
                    <strong>{row.sender}</strong>
                    <small>Trusted {new Date(row.created_at).toLocaleDateString()}</small>
                  </span>
                  <button
                    type="button"
                    className="pb-settings-icon-btn"
                    aria-label={`Stop always loading images from ${row.sender}`}
                    onClick={() => void removeTrusted(row.sender)}
                  >
                    <X className="h-4 w-4" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="pb-settings-empty-copy">No sender-specific image permissions yet.</p>
          )}
        </div>
      </SettingsCard>
      <InlineError message={error ?? identities.error ?? trusted.error} />
    </>
  );
}

function AccountSection() {
  const account = useAsyncData(() => postbox.account(), [], "Account details could not be loaded.");

  if (account.loading) return <SettingsLoading />;
  if (!account.data) return <InlineError message={account.error} />;

  return <AccountLoaded account={account.data} />;
}

function AccountLoaded({ account }: { account: AccountInfo }) {
  const percent = account.storage.percent ?? 0;
  return (
    <>
      <SettingsCard className="pb-account-hero">
        <div className="pb-account-avatar">{initials(account.full_name || account.email)}</div>
        <div>
          <h2>{account.full_name || account.email}</h2>
          <p>{account.email}</p>
        </div>
        <span className="pb-settings-pill">Primary mailbox</span>
      </SettingsCard>

      <SettingsCard title="Mailbox details">
        <dl className="pb-details-table">
          <div><dt>Full name</dt><dd>{account.full_name || "—"}</dd></div>
          <div><dt>Email address</dt><dd>{account.email}</dd></div>
          <div><dt>Organization</dt><dd>{account.organization || "—"}</dd></div>
        </dl>
      </SettingsCard>

      <SettingsCard title="Mailbox storage">
        {account.storage.available && account.storage.quota_mb ? (
          <>
            <div className="pb-storage-total">
              <span>
                {formatStorage(account.storage.used_mb)}
                <small>used</small>
              </span>
              <p>{formatStorage(account.storage.quota_mb)} total</p>
            </div>
            <div className="pb-storage-track">
              <span style={{ width: `${Math.min(percent, 100)}%` }} />
            </div>
            <p className="pb-settings-note">
              {percent}% of your mailbox quota is currently in use. Storage is measured by the
              mail engine; PostBox does not invent a messages/attachments breakdown.
            </p>
          </>
        ) : (
          <p className="pb-settings-empty-copy">Live storage usage is unavailable right now.</p>
        )}
      </SettingsCard>
    </>
  );
}

function IdentitiesSection({ onOpenMail }: { onOpenMail: () => void }) {
  const identities = useAsyncData(() => postbox.identities(), [], "Identities could not be loaded.");
  const { preferences, updatePreferences } = usePostBox();
  const [error, setError] = useState<string | null>(null);

  const setDefault = async (identity: Identity) => {
    setError(null);
    try {
      await updatePreferences({ default_identity: identity.address });
    } catch (caught) {
      setError(describePostBoxError(caught, "The default sender could not be changed."));
    }
  };

  return (
    <>
      <SettingsCard
        title="Sender identities"
        description="These addresses come from your organization and cannot be invented or edited in PostBox."
      >
        {identities.loading ? (
          <SettingsLoading inline />
        ) : identities.data?.results.length ? (
          <div className="pb-identity-list">
            {identities.data.results.map((identity) => {
              const selected =
                preferences.default_identity === identity.address ||
                (!preferences.default_identity && identity.is_primary);
              return (
                <article key={identity.address}>
                  <div className="pb-identity-avatar">
                    {initials(identity.name || identity.address)}
                  </div>
                  <div className="pb-identity-copy">
                    <strong>{identity.name || identity.address}</strong>
                    <p>{identity.address}</p>
                  </div>
                  <div className="pb-identity-tags">
                    {identity.is_primary && <span className="pb-settings-pill">Primary</span>}
                    {identity.kind === "alias" && <span className="pb-settings-pill neutral">Alias</span>}
                    {selected && <span className="pb-settings-pill selected">Default</span>}
                  </div>
                  {!selected && (
                    <button
                      type="button"
                      className="pb-settings-text-btn"
                      onClick={() => void setDefault(identity)}
                    >
                      Make default
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="pb-settings-empty-copy">No sending identities are available.</p>
        )}
      </SettingsCard>
      <div className="pb-info-box">
        <AtSign className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>Identity management stays with your organization</strong>
          <p>You can choose your default sender here, but aliases are provisioned outside PostBox.</p>
          <button type="button" onClick={onOpenMail}>Open mail preferences</button>
        </div>
      </div>
      <InlineError message={error ?? identities.error} />
    </>
  );
}

function SignaturesSection() {
  const data = useAsyncData(() => postbox.signatures(), [], "Signatures could not be loaded.");
  const [editing, setEditing] = useState<Signature | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remove = async (signature: Signature) => {
    if (!window.confirm(`Delete “${signature.name}”? It will no longer be available in the composer.`)) {
      return;
    }
    setError(null);
    try {
      await postbox.deleteSignature(signature.id);
      await data.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature could not be deleted."));
    }
  };

  const toggleDefault = async (
    signature: Signature,
    key: "use_for_new" | "use_for_replies",
    value: boolean,
  ) => {
    setError(null);
    try {
      await postbox.updateSignature(signature.id, { [key]: value });
      await data.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature setting could not be saved."));
    }
  };

  return (
    <>
      <div className="pb-section-toolbar">
        <p>{data.data?.results.length ?? 0} signatures</p>
        <button type="button" className="pb-settings-primary" onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          New signature
        </button>
      </div>

      {data.loading ? (
        <SettingsLoading />
      ) : data.data?.results.length ? (
        <div className="pb-signature-list">
          {data.data.results.map((signature) => (
            <article className="pb-signature-card" key={signature.id}>
              <header>
                <div>
                  <h2>{signature.name}</h2>
                  <span>{signature.kind.toUpperCase()} signature</span>
                </div>
                <div>
                  <button type="button" onClick={() => setEditing(signature)} aria-label={`Edit ${signature.name}`}>
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                  </button>
                  <button type="button" onClick={() => void remove(signature)} aria-label={`Delete ${signature.name}`}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>
              </header>
              <SignaturePreview signature={signature} />
              <footer>
                <label>
                  <input
                    type="checkbox"
                    checked={signature.use_for_new}
                    onChange={(event) =>
                      void toggleDefault(signature, "use_for_new", event.target.checked)
                    }
                  />
                  New messages
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={signature.use_for_replies}
                    onChange={(event) =>
                      void toggleDefault(signature, "use_for_replies", event.target.checked)
                    }
                  />
                  Replies & forwards
                </label>
              </footer>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title="Your sign-off starts here" copy="Create a text, HTML, or image signature." />
      )}

      <InlineError message={error ?? data.error} />
      {(editing || creating) && (
        <SignatureEditor
          signature={editing}
          onClose={() => {
            setEditing(null);
            setCreating(false);
          }}
          onSaved={async () => {
            setEditing(null);
            setCreating(false);
            await data.reload();
          }}
        />
      )}
    </>
  );
}

function SignaturePreview({ signature }: { signature: Signature }) {
  if (signature.kind === "image") {
    return (
      <div className="pb-signature-preview">
        {signature.has_image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={signature.image_url} alt={signature.image_alt} />
        ) : (
          <span>No image uploaded</span>
        )}
      </div>
    );
  }
  if (signature.kind === "html") {
    return (
      <div
        className="pb-signature-preview"
        dangerouslySetInnerHTML={{ __html: signature.html }}
      />
    );
  }
  return <pre className="pb-signature-preview">{signature.text || "(empty)"}</pre>;
}

function SignatureEditor({
  signature,
  onClose,
  onSaved,
}: {
  signature: Signature | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isNew = signature === null;
  const [name, setName] = useState(signature?.name ?? "");
  const [kind, setKind] = useState<SignatureKind>(signature?.kind ?? "text");
  const [text, setText] = useState(signature?.text ?? "");
  const [html, setHtml] = useState(signature?.html ?? "");
  const [alt, setAlt] = useState(signature?.image_alt ?? "");
  const [image, setImage] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const imageInput = useRef<HTMLInputElement | null>(null);

  const save = async () => {
    if (!name.trim()) {
      setError("Enter a signature name.");
      return;
    }
    if (kind === "image" && !alt.trim()) {
      setError("Describe the signature image.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload: Partial<Signature> = {
        name: name.trim(),
        kind,
        text: kind === "text" ? text : kind === "html" ? text : alt,
        html: kind === "html" ? html : "",
        image_alt: kind === "image" ? alt : "",
      };
      const saved = isNew
        ? await postbox.createSignature(payload)
        : await postbox.updateSignature(signature.id, payload);
      if (kind === "image" && image) {
        await postbox.uploadSignatureImage(saved.id, image);
      }
      onSaved();
    } catch (caught) {
      setError(describePostBoxError(caught, "That signature could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={isNew ? "Create signature" : "Edit signature"} onClose={onClose} wide>
      <div className="pb-form-grid">
        <Field label="Signature name">
          <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
        </Field>
        <Field label="Signature type">
          <select value={kind} onChange={(event) => setKind(event.target.value as SignatureKind)}>
            <option value="text">Text</option>
            <option value="html">HTML</option>
            <option value="image">Image</option>
          </select>
        </Field>
      </div>

      {kind === "text" && (
        <Field label="Text content">
          <textarea rows={7} value={text} onChange={(event) => setText(event.target.value)} />
        </Field>
      )}

      {kind === "html" && (
        <>
          <Field label="HTML content">
            <textarea rows={8} value={html} onChange={(event) => setHtml(event.target.value)} />
          </Field>
          <Field label="Plain-text fallback">
            <textarea
              rows={4}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Optional — MateMail can derive this from the HTML."
            />
          </Field>
        </>
      )}

      {kind === "image" && (
        <>
          <Field label="Image description">
            <input value={alt} onChange={(event) => setAlt(event.target.value)} />
          </Field>
          <div className="pb-image-signature-picker">
            <button type="button" onClick={() => imageInput.current?.click()}>
              <ImagePlus className="h-5 w-5" aria-hidden="true" />
              {image ? image.name : "Choose signature image"}
            </button>
            <input
              ref={imageInput}
              className="sr-only"
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              onChange={(event) => setImage(event.target.files?.[0] ?? null)}
            />
            <p>PNG, JPEG, GIF, or WebP. The backend enforces the real size and dimension limits.</p>
          </div>
        </>
      )}

      <InlineError message={error} />
      <div className="pb-modal-footer">
        <button type="button" className="pb-settings-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="pb-settings-primary" disabled={busy} onClick={() => void save()}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          Save signature
        </button>
      </div>
    </Modal>
  );
}

function RulesSection() {
  const rules = useAsyncData(() => postbox.rules(), [], "Rules could not be loaded.");
  const folders = useAsyncData(() => postbox.folders(), [], "Folders could not be loaded.");
  const [editing, setEditing] = useState<Partial<MailRule> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rows = useMemo(
    () => [...(rules.data?.results ?? [])].sort((a, b) => a.position - b.position),
    [rules.data],
  );

  const saveToggle = async (rule: MailRule, enabled: boolean) => {
    setError(null);
    try {
      await postbox.updateRule(rule.id, { enabled });
      await rules.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That rule could not be updated."));
    }
  };

  const remove = async (rule: MailRule) => {
    if (!window.confirm(`Delete “${rule.name}”? Existing messages will not be changed.`)) return;
    setError(null);
    try {
      await postbox.deleteRule(rule.id);
      await rules.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That rule could not be deleted."));
    }
  };

  const move = async (index: number, direction: -1 | 1) => {
    const other = index + direction;
    if (!rows[index] || !rows[other]) return;
    setError(null);
    const first = rows[index];
    const second = rows[other];
    try {
      await postbox.updateRule(first.id, { position: second.position });
      await postbox.updateRule(second.id, { position: first.position });
      await rules.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "The rule order could not be changed."));
    }
  };

  return (
    <>
      <div className="pb-section-toolbar">
        <p>Rules run from top to bottom on new incoming mail.</p>
        <button
          type="button"
          className="pb-settings-primary"
          onClick={() =>
            setEditing({
              name: "",
              position: rows.length ? Math.max(...rows.map((row) => row.position)) + 1 : 0,
              enabled: true,
              field: "from",
              match: "contains",
              value: "",
              action: "move",
              action_folder:
                (folders.data?.results ?? []).find((folder) => !["sent", "drafts", "scheduled"].includes(folder.role))?.name ?? "INBOX",
              stop_processing: false,
            })
          }
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          New rule
        </button>
      </div>

      {rules.loading ? (
        <SettingsLoading />
      ) : rows.length ? (
        <div className="pb-rule-list">
          {rows.map((rule, index) => (
            <article key={rule.id} className="pb-rule-card">
              <div className="pb-rule-order">{index + 1}</div>
              <div className="pb-rule-body">
                <div className="pb-rule-title">
                  <label>
                    <input
                      type="checkbox"
                      checked={rule.enabled}
                      onChange={(event) => void saveToggle(rule, event.target.checked)}
                    />
                    <strong>{rule.name}</strong>
                  </label>
                  <span className={rule.enabled ? "enabled" : ""}>
                    {rule.enabled ? "Enabled" : "Disabled"}
                  </span>
                </div>
                <p>
                  If <b>{rule.field}</b> {rule.match === "is" ? "is exactly" : "contains"}{" "}
                  <b>“{rule.value}”</b>
                </p>
                <p>
                  Then <b>{ruleActionLabel(rule)}</b>
                  {rule.stop_processing ? " · stop processing later rules" : ""}
                </p>
                <div className="pb-rule-actions">
                  <button type="button" onClick={() => setEditing(rule)}>
                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" /> Edit
                  </button>
                  <button
                    type="button"
                    aria-label="Move rule up"
                    disabled={index === 0}
                    onClick={() => void move(index, -1)}
                  >
                    <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    aria-label="Move rule down"
                    disabled={index === rows.length - 1}
                    onClick={() => void move(index, 1)}
                  >
                    <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                  <button type="button" aria-label={`Delete ${rule.name}`} onClick={() => void remove(rule)}>
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState
          title="A more organized inbox"
          copy="Create a rule to move, star, mark as read, or delete matching incoming mail."
        />
      )}

      <div className="pb-info-box">
        <ListFilter className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>Rules apply to incoming mail</strong>
          <p>
            PostBox does not pretend to “apply to existing Inbox” because the backend does not
            expose that operation. New messages are filtered by the real server-side Sieve rules.
          </p>
        </div>
      </div>

      <InlineError message={error ?? rules.error ?? folders.error} />
      {editing && (
        <RuleEditor
          rule={editing}
          folders={folders.data?.results ?? []}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await rules.reload();
          }}
        />
      )}
    </>
  );
}

function ruleActionLabel(rule: MailRule): string {
  if (rule.action === "move") return `move to ${rule.action_folder}`;
  if (rule.action === "mark_read") return "mark as read";
  if (rule.action === "star") return "star";
  return "move to Trash";
}

function RuleEditor({
  rule,
  folders,
  onClose,
  onSaved,
}: {
  rule: Partial<MailRule>;
  folders: Folder[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<Partial<MailRule>>({ ...rule });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const destinations = folders.filter(
    (folder) => !["sent", "drafts", "scheduled", "junk", "trash"].includes(folder.role),
  );

  const save = async () => {
    if (!form.name?.trim() || !form.value?.trim()) {
      setError("Enter a rule name and a value to match.");
      return;
    }
    if (form.action === "move" && !form.action_folder) {
      setError("Choose a destination folder.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload = {
        ...form,
        name: form.name.trim(),
        value: form.value.trim(),
        action_folder: form.action === "move" ? form.action_folder : "",
      };
      if (form.id) await postbox.updateRule(form.id, payload);
      else await postbox.createRule(payload);
      await onSaved();
    } catch (caught) {
      setError(describePostBoxError(caught, "That rule could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={form.id ? "Edit rule" : "Create rule"} onClose={onClose} wide>
      <Field label="Rule name">
        <input
          value={form.name ?? ""}
          onChange={(event) => setForm({ ...form, name: event.target.value })}
          autoFocus
        />
      </Field>
      <h3 className="pb-form-subheading">When an incoming message matches</h3>
      <div className="pb-form-grid">
        <Field label="Field">
          <select
            value={form.field}
            onChange={(event) => setForm({ ...form, field: event.target.value as MailRule["field"] })}
          >
            <option value="from">From</option>
            <option value="to">To or Cc</option>
            <option value="subject">Subject</option>
          </select>
        </Field>
        <Field label="Matching">
          <select
            value={form.match}
            onChange={(event) => setForm({ ...form, match: event.target.value as MailRule["match"] })}
          >
            <option value="contains">Contains</option>
            <option value="is">Is exactly</option>
          </select>
        </Field>
      </div>
      <Field label="Value">
        <input
          value={form.value ?? ""}
          onChange={(event) => setForm({ ...form, value: event.target.value })}
        />
      </Field>
      <h3 className="pb-form-subheading">Do this</h3>
      <div className="pb-form-grid">
        <Field label="Action">
          <select
            value={form.action}
            onChange={(event) => setForm({ ...form, action: event.target.value as MailRule["action"] })}
          >
            <option value="move">Move to folder</option>
            <option value="star">Star</option>
            <option value="mark_read">Mark as read</option>
            <option value="delete">Move to Trash</option>
          </select>
        </Field>
        {form.action === "move" && (
          <Field label="Destination">
            <select
              value={form.action_folder}
              onChange={(event) => setForm({ ...form, action_folder: event.target.value })}
            >
              {destinations.map((folder) => (
                <option key={folder.name} value={folder.name}>{folder.name}</option>
              ))}
            </select>
          </Field>
        )}
      </div>
      <ToggleForm
        label="Enable this rule"
        checked={form.enabled ?? true}
        onChange={(enabled) => setForm({ ...form, enabled })}
      />
      <ToggleForm
        label="Stop processing additional rules"
        description="Skip later rules after this one matches."
        checked={form.stop_processing ?? false}
        onChange={(stop_processing) => setForm({ ...form, stop_processing })}
      />
      <InlineError message={error} />
      <div className="pb-modal-footer">
        <button type="button" className="pb-settings-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="pb-settings-primary" disabled={busy} onClick={() => void save()}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          Save rule
        </button>
      </div>
    </Modal>
  );
}

function VacationSection() {
  const data = useAsyncData(() => postbox.vacation(), [], "Automatic reply settings could not be loaded.");
  if (data.loading) return <SettingsLoading />;
  if (!data.data) return <InlineError message={data.error} />;
  return <VacationForm key={JSON.stringify(data.data)} initial={data.data} onSaved={data.reload} />;
}

function VacationForm({
  initial,
  onSaved,
}: {
  initial: Vacation;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<Vacation>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    if (form.enabled && !form.message.trim()) {
      setError("An automatic reply needs a message.");
      return;
    }
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const result = await postbox.saveVacation({
        ...form,
        starts_at: fromLocalInput(form.starts_at),
        ends_at: fromLocalInput(form.ends_at),
      });
      setForm(result);
      setSaved(true);
      await onSaved();
    } catch (caught) {
      setError(describePostBoxError(caught, "The automatic reply could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <SettingsCard>
        <ToggleRow
          label="Automatic reply"
          description={form.enabled ? "Your server-side vacation response is enabled." : "No automatic response is being sent."}
          checked={form.enabled}
          onChange={(enabled) => setForm({ ...form, enabled })}
        />
      </SettingsCard>

      <SettingsCard title="Response">
        <Field label="Subject">
          <input
            value={form.subject}
            onChange={(event) => setForm({ ...form, subject: event.target.value })}
          />
        </Field>
        <Field label="Message">
          <textarea
            rows={6}
            value={form.message}
            onChange={(event) => setForm({ ...form, message: event.target.value })}
          />
        </Field>
        <div className="pb-form-grid">
          <Field label="Starts">
            <input
              type="datetime-local"
              value={toLocalInput(form.starts_at)}
              onChange={(event) =>
                setForm({ ...form, starts_at: event.target.value || null })
              }
            />
          </Field>
          <Field label="Ends">
            <input
              type="datetime-local"
              value={toLocalInput(form.ends_at)}
              onChange={(event) =>
                setForm({ ...form, ends_at: event.target.value || null })
              }
            />
          </Field>
        </div>
        <Field label="Reply to the same sender">
          <select
            value={form.repeat_days}
            onChange={(event) => setForm({ ...form, repeat_days: Number(event.target.value) })}
          >
            <option value={1}>At most once per day</option>
            <option value={3}>At most once every 3 days</option>
            <option value={7}>At most once every 7 days</option>
            <option value={14}>At most once every 14 days</option>
            <option value={30}>At most once every 30 days</option>
          </select>
        </Field>
      </SettingsCard>

      <div className="pb-settings-savebar">
        <span>{saved ? "Automatic reply saved." : "Sieve handles real incoming mail on the server."}</span>
        <button type="button" className="pb-settings-primary" disabled={busy} onClick={() => void save()}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          Save automatic reply
        </button>
      </div>
      <InlineError message={error} />
    </>
  );
}

function ForwardingSection() {
  const data = useAsyncData(() => postbox.forwarding(), [], "Forwarding details could not be loaded.");

  if (data.loading) return <SettingsLoading />;
  if (!data.data) return <InlineError message={data.error} />;

  const enabled = data.data.results.filter((row) => row.status === "active");
  return (
    <>
      <SettingsCard>
        <div className="pb-forward-status">
          <div className="pb-forward-icon"><Forward className="h-6 w-6" aria-hidden="true" /></div>
          <div>
            <h2>{enabled.length ? "Forwarding is active" : "Forwarding is off"}</h2>
            <p>
              {enabled.length
                ? `${enabled.length} forwarding destination${enabled.length === 1 ? "" : "s"} configured.`
                : "Incoming messages stay in this mailbox unless your organization changes this setting."}
            </p>
          </div>
          <span className={`pb-settings-pill ${enabled.length ? "" : "neutral"}`}>
            {enabled.length ? "Active" : "Disabled"}
          </span>
        </div>

        {data.data.results.length > 0 && (
          <div className="pb-forward-list">
            {data.data.results.map((row, index) => (
              <div key={`${row.destination}-${index}`}>
                <span>
                  <strong>{row.destination}</strong>
                  <small>{row.keep_copy ? "Keep a copy in this mailbox" : "Do not keep a copy"}</small>
                </span>
                <span className="pb-settings-pill neutral">{row.status}</span>
              </div>
            ))}
          </div>
        )}
      </SettingsCard>
      <div className="pb-info-box">
        <Shield className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>Managed by your organization</strong>
          <p>{data.data.detail}</p>
        </div>
      </div>
    </>
  );
}

function NotificationsSection() {
  const { preferences, updatePreferences } = usePostBox();
  const [error, setError] = useState<string | null>(null);

  const save = async (patch: Parameters<typeof updatePreferences>[0]) => {
    setError(null);
    try {
      await updatePreferences(patch);
    } catch (caught) {
      setError(describePostBoxError(caught, "That notification setting could not be saved."));
    }
  };

  return (
    <>
      <SettingsCard title="While PostBox is open">
        <ToggleRow
          label="In-app notifications"
          description="Show an alert in PostBox when the web client receives a supported new-message event."
          checked={preferences.notify_in_app}
          onChange={(value) => void save({ notify_in_app: value })}
        />
        <ToggleRow
          label="Notification sound"
          description="Play a sound with supported in-app new-message alerts."
          checked={preferences.notify_sound}
          disabled={!preferences.notify_in_app}
          onChange={(value) => void save({ notify_sound: value })}
        />
      </SettingsCard>
      <div className="pb-info-box">
        <Bell className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>No fake browser push controls</strong>
          <p>
            The PostBox web client does not currently register browser Notification API or live
            WebSocket/SSE delivery, so this page does not pretend that browser push is enabled.
            Native app registrations are handled by supported clients separately.
          </p>
        </div>
      </div>
      <InlineError message={error} />
    </>
  );
}

function SecuritySection() {
  const sessions = useAsyncData(() => postbox.sessions(), [], "Active sessions could not be loaded.");
  const { signOut } = usePostBox();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmNext, setConfirmNext] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const changePassword = async () => {
    if (!current || !next) {
      setError("Enter your current and new password.");
      return;
    }
    if (next !== confirmNext) {
      setError("The new passwords do not match.");
      return;
    }
    setPasswordBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await postbox.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirmNext("");
      setNotice(
        result.other_sessions_revoked
          ? `Password changed. ${result.other_sessions_revoked} other session${result.other_sessions_revoked === 1 ? "" : "s"} revoked.`
          : "Password changed.",
      );
      await sessions.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "Your password could not be changed."));
    } finally {
      setPasswordBusy(false);
    }
  };

  const revoke = async (row: SessionRow) => {
    setSessionBusy(row.id);
    setError(null);
    try {
      await postbox.revokeSession(row.id);
      await sessions.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That session could not be revoked."));
    } finally {
      setSessionBusy(null);
    }
  };

  const signOutEverywhere = async () => {
    if (!window.confirm("Sign out every PostBox session, including this one?")) return;
    setError(null);
    try {
      await postbox.logoutAll();
      await signOut();
      window.location.assign("/postbox/login");
    } catch (caught) {
      setError(describePostBoxError(caught, "All sessions could not be signed out."));
    }
  };

  return (
    <>
      <SettingsCard
        title="Change password"
        description="Changing the mailbox password also revokes every other PostBox session."
      >
        <div className="pb-password-grid">
          <Field label="Current password">
            <input
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
            />
          </Field>
          <Field label="New password">
            <input
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(event) => setNext(event.target.value)}
            />
          </Field>
          <Field label="Confirm new password">
            <input
              type="password"
              autoComplete="new-password"
              value={confirmNext}
              onChange={(event) => setConfirmNext(event.target.value)}
            />
          </Field>
        </div>
        <button
          type="button"
          className="pb-settings-primary"
          disabled={passwordBusy}
          onClick={() => void changePassword()}
        >
          {passwordBusy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
          Change password
        </button>
        {notice && <p className="pb-settings-success">{notice}</p>}
      </SettingsCard>

      <SettingsCard title="Active sessions">
        {sessions.loading ? (
          <SettingsLoading inline />
        ) : sessions.data?.results.length ? (
          <div className="pb-session-list">
            {sessions.data.results.map((row) => {
              const mobile = /iphone|android|mobile/i.test(row.user_agent);
              return (
                <article key={row.id}>
                  <div className="pb-session-icon">
                    {mobile ? <Smartphone className="h-5 w-5" aria-hidden="true" /> : <Monitor className="h-5 w-5" aria-hidden="true" />}
                  </div>
                  <div className="pb-session-copy">
                    <strong>{friendlyUserAgent(row.user_agent)}</strong>
                    <p>
                      {row.ip_address || "IP unavailable"} · Last active{" "}
                      {new Date(row.last_seen_at).toLocaleString()}
                    </p>
                    <small>
                      Signed in {new Date(row.created_at).toLocaleString()} ·{" "}
                      {row.remembered ? "Remembered device" : "Session only"}
                    </small>
                  </div>
                  {row.current ? (
                    <span className="pb-settings-pill">This device</span>
                  ) : (
                    <button
                      type="button"
                      className="pb-settings-secondary"
                      disabled={sessionBusy === row.id}
                      onClick={() => void revoke(row)}
                    >
                      {sessionBusy === row.id && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                      Revoke
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="pb-settings-empty-copy">No active sessions were returned.</p>
        )}
        <button type="button" className="pb-danger-text" onClick={() => void signOutEverywhere()}>
          Sign out everywhere
        </button>
      </SettingsCard>
      <InlineError message={error ?? sessions.error} />
    </>
  );
}

function ClientSetupSection() {
  const data = useAsyncData(
    async () => (await postbox.me()).mail_client,
    [],
    "Mail client settings could not be loaded.",
  );

  if (data.loading) return <SettingsLoading />;
  if (!data.data) return <InlineError message={data.error} />;
  return <ClientSetupLoaded client={data.data} />;
}

function ClientSetupLoaded({ client }: { client: MailClientSettings }) {
  return (
    <>
      <div className="pb-info-box">
        <Monitor className="h-5 w-5" aria-hidden="true" />
        <div>
          <strong>Live production connection details</strong>
          <p>These values come from MateMail configuration, not prototype placeholders.</p>
        </div>
      </div>

      <SettingsCard title="Your sign-in details">
        <dl className="pb-details-table">
          <div><dt>Username</dt><dd><CopyValue value={client.username} /></dd></div>
          <div><dt>Password</dt><dd>Your mailbox password</dd></div>
        </dl>
      </SettingsCard>

      <div className="pb-server-grid">
        <ServerCard
          title="Incoming mail"
          protocol="IMAP"
          server={client.imap.server}
          port={client.imap.port}
          security={client.imap.encryption}
        />
        <ServerCard
          title="Outgoing mail"
          protocol="SMTP"
          server={client.smtp.server}
          port={client.smtp.port}
          security={client.smtp.encryption}
          auth={client.smtp.auth_required}
        />
      </div>

      <p className="pb-settings-note">
        POP3 and unencrypted IMAP are not offered. Use your full email address as the username.
      </p>
    </>
  );
}

function ServerCard({
  title,
  protocol,
  server,
  port,
  security,
  auth,
}: {
  title: string;
  protocol: string;
  server: string;
  port: number;
  security: string;
  auth?: boolean;
}) {
  return (
    <SettingsCard className="pb-server-card">
      <span className="pb-settings-pill">{protocol}</span>
      <h2>{title}</h2>
      <dl>
        <dt>Server</dt><dd><CopyValue value={server} /></dd>
        <dt>Port</dt><dd><CopyValue value={String(port)} /></dd>
        <dt>Connection security</dt><dd>{security}</dd>
        <dt>Authentication</dt><dd>{auth === false ? "Not required" : "Required · Password"}</dd>
      </dl>
    </SettingsCard>
  );
}

function CopyValue({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  return (
    <span className="pb-copy-value">
      <code>{value}</code>
      <button type="button" onClick={() => void copy()} aria-label={`Copy ${value}`}>
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Copy className="h-3.5 w-3.5" aria-hidden="true" />}
      </button>
    </span>
  );
}

function Modal({
  title,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="pb-settings-modal-backdrop" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`pb-settings-modal ${wide ? "wide" : ""}`}>
        <header>
          <h2>{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>
        <div className="pb-settings-modal-body">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="pb-settings-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ToggleForm({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="pb-form-toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
    </label>
  );
}

function SettingsLoading({ inline = false }: { inline?: boolean }) {
  return (
    <div className={inline ? "pb-settings-loading inline" : "pb-settings-loading"}>
      <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
    </div>
  );
}

function EmptyState({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="pb-settings-empty">
      <HardDrive className="h-7 w-7" aria-hidden="true" />
      <strong>{title}</strong>
      <p>{copy}</p>
    </div>
  );
}

function initials(value: string): string {
  const parts = value.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return (parts.slice(0, 2).map((part) => part[0]).join("") || "M").toUpperCase();
}

function formatStorage(value: number | null): string {
  if (value === null) return "—";
  if (value < 1024) return `${value.toFixed(value < 10 ? 1 : 0)} MB`;
  return `${(value / 1024).toFixed(1)} GB`;
}

function toLocalInput(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function fromLocalInput(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function friendlyUserAgent(value: string): string {
  if (!value) return "Unknown device";
  const browser =
    /Edg\//.test(value) ? "Edge" :
    /Chrome\//.test(value) ? "Chrome" :
    /Firefox\//.test(value) ? "Firefox" :
    /Safari\//.test(value) ? "Safari" : "Browser";
  const platform =
    /iPhone|iPad/.test(value) ? "iOS" :
    /Android/.test(value) ? "Android" :
    /Windows/.test(value) ? "Windows" :
    /Mac OS X/.test(value) ? "macOS" :
    /Linux/.test(value) ? "Linux" : "device";
  return `${browser} on ${platform}`;
}
