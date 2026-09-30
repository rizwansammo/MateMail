"use client";

/**
 * Contacts: the mailbox's own address book.
 *
 * Scoped to this mailbox server-side — an organization does not share one
 * address book, and a colleague's contacts are not a colleague's business.
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Pencil, Plus, Search, Send, Trash2, X } from "lucide-react";

import { describePostBoxError } from "@/contexts/postbox-context";
import { useAsyncData } from "@/components/postbox/use-async";
import { postbox, type Contact } from "@/lib/postbox-api";

const EMPTY: Partial<Contact> = {
  name: "", email: "", company: "", title: "", phone: "", notes: "",
};

export default function LegacyContactsPage() {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<Partial<Contact> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setSearch(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const query_ = useAsyncData(
    () => postbox.contacts(search || undefined),
    [search],
    "Contacts could not be loaded.",
  );

  const contacts = query_.data?.results ?? [];
  const loading = query_.loading;
  const load = query_.reload;

  const save = useCallback(async () => {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      if (editing.id) {
        await postbox.updateContact(editing.id, editing);
      } else {
        await postbox.createContact(editing);
      }
      setEditing(null);
      await load();
    } catch (caught) {
      setError(describePostBoxError(caught, "That contact could not be saved."));
    } finally {
      setBusy(false);
    }
  }, [editing, load]);

  const remove = useCallback(
    async (contact: Contact) => {
      setBusy(true);
      try {
        await postbox.deleteContact(contact.id);
        await load();
      } catch (caught) {
        setError(describePostBoxError(caught, "That contact could not be removed."));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  return (
    <div className="pb-scroll h-full">
      <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">Contacts</h1>
          <button
            type="button"
            className="pb-btn pb-btn-primary ml-auto"
            onClick={() => setEditing({ ...EMPTY })}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            Add contact
          </button>
        </div>

        <div className="relative mb-4 max-w-sm">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
            style={{ color: "var(--pb-subtle)" }}
            aria-hidden="true"
          />
          <input
            className="pb-input"
            style={{ paddingLeft: "1.9rem" }}
            placeholder="Search contacts"
            aria-label="Search contacts"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>

        {(error ?? query_.error) && (
          <p className="mb-3 text-xs" role="alert" style={{ color: "var(--pb-danger)" }}>
            {error ?? query_.error}
          </p>
        )}

        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
          </div>
        ) : contacts.length === 0 ? (
          <div className="pb-panel px-6 py-12 text-center">
            <p className="text-sm font-medium">No contacts yet</p>
            <p className="mt-1 text-xs pb-muted">
              Add the people you write to most, so addresses complete as you type.
            </p>
          </div>
        ) : (
          <ul className="pb-panel divide-y" style={{ borderColor: "var(--pb-border)" }}>
            {contacts.map((contact) => (
              <li
                key={contact.id}
                className="flex flex-wrap items-center gap-3 px-3 py-2.5"
                style={{ borderColor: "var(--pb-border)" }}
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {contact.name || contact.email}
                  </p>
                  <p className="truncate text-xs pb-muted">{contact.email}</p>
                  {(contact.company || contact.title) && (
                    <p className="truncate text-xs pb-subtle">
                      {[contact.title, contact.company].filter(Boolean).join(" · ")}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <Link
                    href={`/postbox?compose=new&to=${encodeURIComponent(contact.email)}`}
                    className="pb-btn pb-btn-plain"
                    aria-label={`Write to ${contact.email}`}
                  >
                    <Send className="h-3.5 w-3.5" aria-hidden="true" />
                  </Link>
                  <button
                    type="button"
                    className="pb-btn pb-btn-plain"
                    aria-label={`Edit ${contact.email}`}
                    onClick={() => setEditing(contact)}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="pb-btn pb-btn-plain"
                    aria-label={`Delete ${contact.email}`}
                    disabled={busy}
                    onClick={() => void remove(contact)}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {editing && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgb(19 28 39 / 0.45)" }}
          role="dialog"
          aria-modal="true"
          aria-label="Contact"
        >
          <div className="pb-panel w-full max-w-md p-4" style={{ boxShadow: "var(--pb-shadow-lg)" }}>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold">
                {editing.id ? "Edit contact" : "New contact"}
              </h2>
              <button
                type="button"
                className="pb-btn pb-btn-plain"
                aria-label="Close"
                onClick={() => setEditing(null)}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>

            <div className="space-y-2">
              {([
                ["name", "Name", "text"],
                ["email", "Email", "email"],
                ["company", "Company", "text"],
                ["title", "Job title", "text"],
                ["phone", "Phone", "tel"],
              ] as const).map(([key, label, type]) => (
                <div key={key}>
                  <label htmlFor={`pb-contact-${key}`} className="pb-label">
                    {label}
                  </label>
                  <input
                    id={`pb-contact-${key}`}
                    className="pb-input mt-1"
                    type={type}
                    required={key === "email"}
                    value={(editing[key] as string) ?? ""}
                    onChange={(event) =>
                      setEditing({ ...editing, [key]: event.target.value })
                    }
                  />
                </div>
              ))}
              <div>
                <label htmlFor="pb-contact-notes" className="pb-label">
                  Notes
                </label>
                <textarea
                  id="pb-contact-notes"
                  className="pb-textarea mt-1"
                  rows={3}
                  value={editing.notes ?? ""}
                  onChange={(event) =>
                    setEditing({ ...editing, notes: event.target.value })
                  }
                />
              </div>
            </div>

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="pb-btn pb-btn-ghost"
                onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="pb-btn pb-btn-primary"
                disabled={busy || !editing.email}
                onClick={() => void save()}
              >
                {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
