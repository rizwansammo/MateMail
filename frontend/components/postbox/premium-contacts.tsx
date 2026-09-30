"use client";

import {
  Building2,
  Loader2,
  Mail,
  Pencil,
  Phone,
  Plus,
  Search,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useAsyncData } from "@/components/postbox/use-async";
import { describePostBoxError } from "@/contexts/postbox-context";
import { postbox, type Contact } from "@/lib/postbox-api";

const EMPTY: Partial<Contact> = {
  name: "",
  email: "",
  company: "",
  title: "",
  phone: "",
  notes: "",
};

export default function PremiumContacts() {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Partial<Contact> | null>(null);
  const [viewing, setViewing] = useState<Contact | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  const data = useAsyncData(
    () => postbox.contacts(search || undefined),
    [search],
    "Contacts could not be loaded.",
  );

  const contacts = useMemo(
    () =>
      [...(data.data?.results ?? [])].sort((a, b) =>
        (a.name || a.email).localeCompare(b.name || b.email),
      ),
    [data.data],
  );

  const save = useCallback(async () => {
    if (!editing) return;
    if (!editing.email?.trim()) {
      setError("Enter an email address.");
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const payload = {
        ...editing,
        name: editing.name?.trim() ?? "",
        email: editing.email.trim(),
        company: editing.company?.trim() ?? "",
        title: editing.title?.trim() ?? "",
        phone: editing.phone?.trim() ?? "",
        notes: editing.notes?.trim() ?? "",
      };
      if (editing.id) await postbox.updateContact(editing.id, payload);
      else await postbox.createContact(payload);
      setEditing(null);
      await data.reload();
    } catch (caught) {
      setError(describePostBoxError(caught, "That contact could not be saved."));
    } finally {
      setBusy(false);
    }
  }, [data, editing]);

  const remove = useCallback(
    async (contact: Contact) => {
      if (
        !window.confirm(
          `Delete ${contact.name || contact.email}? Existing messages will not be changed.`,
        )
      ) {
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await postbox.deleteContact(contact.id);
        if (viewing?.id === contact.id) setViewing(null);
        await data.reload();
      } catch (caught) {
        setError(describePostBoxError(caught, "That contact could not be deleted."));
      } finally {
        setBusy(false);
      }
    },
    [data, viewing],
  );

  return (
    <div className="pb-premium-contacts pb-scroll">
      <div className="pb-contacts-inner">
        <header className="pb-contacts-heading">
          <div>
            <span>YOUR PEOPLE</span>
            <h1>Contacts</h1>
            <p>The people you write to most, saved only in this mailbox.</p>
          </div>
          <button
            type="button"
            className="pb-settings-primary"
            onClick={() => setEditing({ ...EMPTY })}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            New contact
          </button>
        </header>

        <div className="pb-contacts-searchbar">
          <Search className="h-[18px] w-[18px]" aria-hidden="true" />
          <input
            aria-label="Search contacts"
            placeholder="Search names, emails, or companies"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span>{contacts.length} {contacts.length === 1 ? "contact" : "contacts"}</span>
        </div>

        {(error ?? data.error) && (
          <p className="pb-settings-error" role="alert">{error ?? data.error}</p>
        )}

        {data.loading ? (
          <div className="pb-contacts-loading">
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
          </div>
        ) : contacts.length ? (
          <div className="pb-contacts-grid">
            {contacts.map((contact) => (
              <article className="pb-contact-card" key={contact.id}>
                <div className="pb-contact-card-top">
                  <button
                    type="button"
                    className="pb-contact-avatar"
                    onClick={() => setViewing(contact)}
                    aria-label={`Open ${contact.name || contact.email}`}
                  >
                    {initials(contact.name || contact.email)}
                  </button>
                  <button
                    type="button"
                    className="pb-settings-icon-btn"
                    aria-label={`Edit ${contact.name || contact.email}`}
                    onClick={() => setEditing({ ...contact })}
                  >
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                  </button>
                </div>

                <button
                  type="button"
                  className="pb-contact-name"
                  onClick={() => setViewing(contact)}
                >
                  <h2>{contact.name || contact.email}</h2>
                  <p>{contact.title || "Contact"}</p>
                </button>

                <div className="pb-contact-meta">
                  <p>
                    <Building2 className="h-4 w-4" aria-hidden="true" />
                    <span>{contact.company || "No company added"}</span>
                  </p>
                  <p>
                    <Mail className="h-4 w-4" aria-hidden="true" />
                    <span>{contact.email}</span>
                  </p>
                  {contact.phone && (
                    <p>
                      <Phone className="h-4 w-4" aria-hidden="true" />
                      <span>{contact.phone}</span>
                    </p>
                  )}
                </div>

                <footer>
                  <Link
                    href={`/postbox?compose=new&to=${encodeURIComponent(contact.email)}`}
                  >
                    <Mail className="h-4 w-4" aria-hidden="true" />
                    Send message
                  </Link>
                  <button
                    type="button"
                    className="pb-settings-icon-btn"
                    aria-label={`Delete ${contact.name || contact.email}`}
                    disabled={busy}
                    onClick={() => void remove(contact)}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </footer>
              </article>
            ))}
          </div>
        ) : (
          <div className="pb-contacts-empty">
            <UserRound className="h-8 w-8" aria-hidden="true" />
            <h2>{query ? "No contacts found" : "Your address book is ready"}</h2>
            <p>
              {query
                ? "Try another name, email address, or company."
                : "Add someone you work with and they will appear in compose suggestions."}
            </p>
          </div>
        )}
      </div>

      {editing && (
        <ContactEditor
          contact={editing}
          busy={busy}
          error={error}
          onChange={setEditing}
          onClose={() => setEditing(null)}
          onSave={() => void save()}
        />
      )}

      {viewing && (
        <ContactViewer
          contact={viewing}
          onClose={() => setViewing(null)}
          onEdit={() => {
            setEditing({ ...viewing });
            setViewing(null);
          }}
        />
      )}
    </div>
  );
}

function ContactEditor({
  contact,
  busy,
  error,
  onChange,
  onClose,
  onSave,
}: {
  contact: Partial<Contact>;
  busy: boolean;
  error: string | null;
  onChange: (contact: Partial<Contact>) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <div className="pb-settings-modal-backdrop" role="dialog" aria-modal="true" aria-label="Contact">
      <div className="pb-settings-modal">
        <header>
          <h2>{contact.id ? "Edit contact" : "New contact"}</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>
        <div className="pb-settings-modal-body">
          <div className="pb-form-grid">
            <Field label="Full name">
              <input
                autoFocus
                value={contact.name ?? ""}
                onChange={(event) => onChange({ ...contact, name: event.target.value })}
              />
            </Field>
            <Field label="Email address">
              <input
                type="email"
                value={contact.email ?? ""}
                onChange={(event) => onChange({ ...contact, email: event.target.value })}
              />
            </Field>
            <Field label="Company">
              <input
                value={contact.company ?? ""}
                onChange={(event) => onChange({ ...contact, company: event.target.value })}
              />
            </Field>
            <Field label="Job title">
              <input
                value={contact.title ?? ""}
                onChange={(event) => onChange({ ...contact, title: event.target.value })}
              />
            </Field>
            <Field label="Phone">
              <input
                type="tel"
                value={contact.phone ?? ""}
                onChange={(event) => onChange({ ...contact, phone: event.target.value })}
              />
            </Field>
          </div>
          <Field label="Notes">
            <textarea
              rows={4}
              value={contact.notes ?? ""}
              onChange={(event) => onChange({ ...contact, notes: event.target.value })}
            />
          </Field>
          {error && <p className="pb-settings-error" role="alert">{error}</p>}
          <div className="pb-modal-footer">
            <button type="button" className="pb-settings-secondary" onClick={onClose}>Cancel</button>
            <button
              type="button"
              className="pb-settings-primary"
              disabled={busy || !contact.email?.trim()}
              onClick={onSave}
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              Save contact
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ContactViewer({
  contact,
  onClose,
  onEdit,
}: {
  contact: Contact;
  onClose: () => void;
  onEdit: () => void;
}) {
  return (
    <div
      className="pb-settings-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={contact.name || contact.email}
    >
      <div className="pb-settings-modal pb-contact-viewer">
        <header>
          <h2>Contact</h2>
          <button type="button" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </header>
        <div className="pb-settings-modal-body">
          <div className="pb-contact-viewer-head">
            <div className="pb-contact-avatar large">{initials(contact.name || contact.email)}</div>
            <div>
              <h3>{contact.name || contact.email}</h3>
              <p>{[contact.title, contact.company].filter(Boolean).join(" · ") || "Contact"}</p>
            </div>
          </div>

          <dl className="pb-details-table">
            <div><dt>Email</dt><dd>{contact.email}</dd></div>
            <div><dt>Phone</dt><dd>{contact.phone || "—"}</dd></div>
            <div><dt>Company</dt><dd>{contact.company || "—"}</dd></div>
            <div><dt>Job title</dt><dd>{contact.title || "—"}</dd></div>
          </dl>

          <div className="pb-contact-notes">
            <strong>Notes</strong>
            <p>{contact.notes || "No notes yet."}</p>
          </div>

          <div className="pb-modal-footer">
            <button type="button" className="pb-settings-secondary" onClick={onEdit}>
              <Pencil className="h-4 w-4" aria-hidden="true" /> Edit
            </button>
            <Link
              className="pb-settings-primary"
              href={`/postbox?compose=new&to=${encodeURIComponent(contact.email)}`}
            >
              <Mail className="h-4 w-4" aria-hidden="true" />
              Send message
            </Link>
          </div>
        </div>
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

function initials(value: string): string {
  const parts = value.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return (parts.slice(0, 2).map((part) => part[0]).join("") || "M").toUpperCase();
}
