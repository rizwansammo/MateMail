"use client";

/**
 * The PostBox shell: folder rail, header, and the guard.
 *
 * The guard is a convenience. Every `/api/postbox/` endpoint is authenticated
 * by the session cookie server-side; this stops somebody seeing a mail client
 * frame full of failed requests before the redirect lands.
 */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Archive,
  ChevronDown,
  Clock,
  FileText,
  Folder as FolderIcon,
  Tag,
  Plus,
  MoreHorizontal,
  ChevronRight,
  Inbox,
  Loader2,
  LogOut,
  PenLine,
  Menu,
  Moon,
  Monitor,
  Search,
  Send,
  Settings,
  Settings2,
  ShieldAlert,
  Star,
  Sun,
  User,
  UserPlus,
  Users,
  Trash2,
  X,
} from "lucide-react";

import { usePostBox } from "@/contexts/postbox-context";
import {
  POSTBOX_MAILBOX_EVENT,
  announcePostBoxMailboxChange,
  installPostBoxCrossTabSync,
  type PostBoxMailboxChange,
} from "@/lib/postbox-realtime";
import {
  postbox,
  type Folder,
  type MailLabel,
  type SavedPostBoxAccount,
} from "@/lib/postbox-api";

/**
 * The standard folders, in the order they belong.
 *
 * Reading order first — Inbox, Starred, Scheduled — then the things you
 * sent, then storage, then the two bins. Starred and Scheduled used to be
 * appended after this list, which put them below Trash; they are among the
 * most-used views and were the hardest to find.
 *
 * Every entry has its own icon. They previously shared one because the
 * folders were falling through to the custom branch, which hardcoded
 * `Archive` for everything.
 */
const FOLDER_COLOR_DEFAULT = "#2563eb";
const LABEL_COLOR_DEFAULT = "#9333ea";
const ORGANIZE_COLORS = [
  ["#64748b", "Slate"], ["#2563eb", "Blue"], ["#0891b2", "Cyan"],
  ["#059669", "Green"], ["#65a30d", "Lime"], ["#d97706", "Amber"],
  ["#ea580c", "Orange"], ["#dc2626", "Red"], ["#9333ea", "Purple"],
  ["#db2777", "Pink"], ["#0d9488", "Teal"],
] as const;

const PINNED: Array<{
  role: string;
  label: string;
  icon: typeof Inbox;
  href?: string;
}> = [
  { role: "inbox", label: "Inbox", icon: Inbox },
  // Not a folder — a filter over INBOX, so it carries its own href.
  { role: "starred", label: "Starred", icon: Star, href: "/postbox?folder=INBOX&starred=true&scope=all" },
  { role: "scheduled", label: "Scheduled", icon: Clock },
  { role: "sent", label: "Sent", icon: Send },
  { role: "drafts", label: "Drafts", icon: FileText },
  { role: "archive", label: "Archive", icon: Archive },
  { role: "junk", label: "Spam", icon: ShieldAlert },
  { role: "trash", label: "Trash", icon: Trash2 },
];

/** Roles the list above already shows, so they cannot also appear below. */
const PINNED_ROLES = new Set(PINNED.map((entry) => entry.role));

export default function PostBoxAppLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { mailbox, preferences, isLoading, signOut, updatePreferences } = usePostBox();
  const router = useRouter();
  const pathname = usePathname();

  const [folders, setFolders] = useState<Folder[]>([]);
  const [railOpen, setRailOpen] = useState(false);
  const folderRefreshTimer = useRef<number | null>(null);
  const refreshFolders = useCallback(
    () => postbox.folders().then((data) => setFolders(data.results)),
    [],
  );
  const scheduleFolderRefresh = useCallback(() => {
    if (folderRefreshTimer.current !== null) {
      window.clearTimeout(folderRefreshTimer.current);
    }
    folderRefreshTimer.current = window.setTimeout(() => {
      folderRefreshTimer.current = null;
      void refreshFolders().catch(() => {
        // Realtime is supplementary. A transient IMAP/API failure must not
        // replace the last known sidebar state with an empty one.
      });
    }, 80);
  }, [refreshFolders]);

  useEffect(() => {
    if (!isLoading && !mailbox) router.replace("/postbox/login");
  }, [isLoading, mailbox, router]);

  useEffect(() => {
    if (!mailbox) return;
    let cancelled = false;
    postbox
      .folders()
      .then((data) => {
        if (!cancelled) setFolders(data.results);
      })
      .catch(() => {
        if (!cancelled) setFolders([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mailbox, pathname]);

  useEffect(() => {
    if (!mailbox) return;

    const refreshFromMailboxEvent = () => scheduleFolderRefresh();
    window.addEventListener(POSTBOX_MAILBOX_EVENT, refreshFromMailboxEvent);
    window.addEventListener("postbox:refresh-folders", refreshFromMailboxEvent);

    const stopCrossTab = installPostBoxCrossTabSync();
    const stream = new EventSource("/api/postbox/events/");
    const onServerEvent = (event: Event) => {
      try {
        const change = JSON.parse((event as MessageEvent<string>).data) as PostBoxMailboxChange;
        if (change?.event_id && change?.kind) {
          // Dispatch locally and mirror to sibling tabs. Consumers de-duplicate
          // by event id, so receiving the same server event in two tabs is safe.
          announcePostBoxMailboxChange(change);
        }
      } catch {
        // A malformed wake-up is ignored; authoritative mailbox state is never
        // carried inside the event itself.
      }
    };
    stream.addEventListener("mailbox", onServerEvent);

    return () => {
      stream.removeEventListener("mailbox", onServerEvent);
      stream.close();
      stopCrossTab();
      window.removeEventListener(POSTBOX_MAILBOX_EVENT, refreshFromMailboxEvent);
      window.removeEventListener("postbox:refresh-folders", refreshFromMailboxEvent);
      if (folderRefreshTimer.current !== null) {
        window.clearTimeout(folderRefreshTimer.current);
        folderRefreshTimer.current = null;
      }
    };
  }, [mailbox, scheduleFolderRefresh]);

  const closeRail = useCallback(() => setRailOpen(false), []);

  if (isLoading) {
    return (
      <div className="pb flex min-h-screen items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  if (!mailbox) return <div className="pb min-h-screen" />;

  return (
    <PremiumPostBoxShell
      folders={folders}
      refreshFolders={refreshFolders}
      mailbox={mailbox}
      preferences={preferences}
      railOpen={railOpen}
      setRailOpen={setRailOpen}
      closeRail={closeRail}
      signOut={signOut}
      updatePreferences={updatePreferences}
    >
      {children}
    </PremiumPostBoxShell>
  );
}

function PremiumPostBoxShell({
  children,
  folders,
  refreshFolders,
  mailbox,
  preferences,
  railOpen,
  setRailOpen,
  closeRail,
  signOut,
  updatePreferences,
}: {
  children: React.ReactNode;
  folders: Folder[];
  refreshFolders: () => Promise<void>;
  mailbox: NonNullable<ReturnType<typeof usePostBox>["mailbox"]>;
  preferences: ReturnType<typeof usePostBox>["preferences"];
  railOpen: boolean;
  setRailOpen: (open: boolean) => void;
  closeRail: () => void;
  signOut: () => Promise<void>;
  updatePreferences: ReturnType<typeof usePostBox>["updatePreferences"];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const accountMenuRef = useRef<HTMLDetailsElement | null>(null);
  const [savedAccounts, setSavedAccounts] = useState<SavedPostBoxAccount[]>([]);
  const [switchingAccount, setSwitchingAccount] = useState<string | null>(null);
  const [accountMenuError, setAccountMenuError] = useState<string | null>(null);
  const [labels, setLabels] = useState<MailLabel[]>([]);
  const [editor, setEditor] = useState<{
    kind: "folder" | "label"; original?: string; id?: string; colorOnly?: boolean;
  } | null>(null);
  const [entryName, setEntryName] = useState("");
  const [entryColor, setEntryColor] = useState(FOLDER_COLOR_DEFAULT);
  const [editorError, setEditorError] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState<{
    name: string; kind: "checking" | "ready" | "error";
    messageCount: number; activeRules: number; error?: string;
  } | null>(null);
  const [deletingFolder, setDeletingFolder] = useState(false);

  const refreshLabels = useCallback(() =>
    postbox.labels().then((value) => setLabels(value.results)), []);
  useEffect(() => {
    void refreshLabels().catch(() => setLabels([]));
    window.addEventListener("postbox:labels-changed", refreshLabels);
    return () => window.removeEventListener("postbox:labels-changed", refreshLabels);
  }, [mailbox.id, refreshLabels]);

  const openEditor = (
    kind: "folder" | "label", original?: string, id?: string,
    color?: string | null, colorOnly = false,
  ) => {
    setEditor({ kind, original, id, colorOnly });
    setEntryName(original || "");
    setEntryColor(color || (kind === "folder" ? FOLDER_COLOR_DEFAULT : LABEL_COLOR_DEFAULT));
    setEditorError("");
  };

  const saveEntry = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editor || !entryName.trim() || saving) return;
    setSaving(true);
    setEditorError("");
    try {
      if (editor.kind === "folder") {
        if (editor.original) {
          if (editor.colorOnly) await postbox.colorFolder(editor.original, entryColor);
          else await postbox.renameFolder(editor.original, entryName.trim(), entryColor);
        } else {
          await postbox.createFolder(entryName.trim(), entryColor);
        }
        await refreshFolders();
        if (editor.original && !editor.colorOnly &&
            new URLSearchParams(window.location.search).get("folder") === editor.original) {
          router.push("/postbox?folder=" + encodeURIComponent(entryName.trim()));
        }
      } else {
        if (editor.id) {
          if (editor.colorOnly) await postbox.colorLabel(editor.id, entryColor);
          else await postbox.renameLabel(editor.id, entryName.trim(), entryColor);
        } else {
          await postbox.createLabel(entryName.trim(), entryColor);
        }
        await refreshLabels();
        window.dispatchEvent(new Event("postbox:labels-changed"));
      }
      setEditor(null);
    } catch (caught) {
      setEditorError(caught instanceof Error ? caught.message : "Could not save. Try again.");
    } finally {
      setSaving(false);
    }
  };

  const deleteEntry = async (kind: "folder" | "label", name: string, id?: string) => {
    if (kind === "folder") {
      setDeleteDialog({ name, kind: "checking", messageCount: 0, activeRules: 0 });
      try {
        const info = await postbox.folderDeleteCheck(name);
        setDeleteDialog({
          name, kind: "ready", messageCount: info.message_count,
          activeRules: info.active_rule_count,
        });
      } catch (caught) {
        setDeleteDialog({
          name, kind: "error", messageCount: 0, activeRules: 0,
          error: caught instanceof Error ? caught.message : "Could not inspect this folder.",
        });
      }
      return;
    }
    if (!window.confirm("Delete label " + name + "? No emails will be deleted.")) return;
    if (!id) return;
    try {
      await postbox.deleteLabel(id);
      await refreshLabels();
      window.dispatchEvent(new Event("postbox:labels-changed"));
      if (new URLSearchParams(window.location.search).get("label") === id) {
        router.push("/postbox?folder=INBOX");
      }
    } catch (caught) {
      window.alert(caught instanceof Error ? caught.message : "Delete failed.");
    }
  };

  const confirmFolderDelete = async () => {
    if (!deleteDialog || deleteDialog.kind !== "ready" ||
        deleteDialog.messageCount || deleteDialog.activeRules || deletingFolder) return;
    const name = deleteDialog.name;
    setDeletingFolder(true);
    try {
      // The backend re-checks the live mailbox, including emails arriving
      // after the preview. No messages are moved or deleted by this action.
      await postbox.deleteFolder(name);
      setDeleteDialog(null);
      await refreshFolders();
      if (new URLSearchParams(window.location.search).get("folder") === name) {
        router.push("/postbox?folder=INBOX");
      }
    } catch (caught) {
      try {
        const info = await postbox.folderDeleteCheck(name);
        setDeleteDialog({
          name, kind: "ready", messageCount: info.message_count,
          activeRules: info.active_rule_count,
          error: caught instanceof Error ? caught.message : "Folder deletion failed.",
        });
      } catch {
        setDeleteDialog({
          name, kind: "error", messageCount: 0, activeRules: 0,
          error: caught instanceof Error ? caught.message : "Folder deletion failed.",
        });
      }
    } finally {
      setDeletingFolder(false);
    }
  };

  useEffect(() => {
    let cancelled = false;
    postbox
      .accounts()
      .then((value) => {
        if (!cancelled) setSavedAccounts(value.results);
      })
      .catch(() => {
        if (!cancelled) setSavedAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mailbox.id]);

  useEffect(() => {
    // Native <details> does not close when the user clicks elsewhere. Keep
    // built-in summary/keyboard behavior and dismiss only outside the card.
    const dismissOutside = (event: PointerEvent) => {
      const menu = accountMenuRef.current;
      if (menu?.open && event.target instanceof Node &&
          !menu.contains(event.target)) {
        menu.open = false;
      }
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      const menu = accountMenuRef.current;
      if (!menu?.open || event.key !== "Escape") return;
      event.preventDefault();
      menu.open = false;
      menu.querySelector("summary")?.focus();
    };
    document.addEventListener("pointerdown", dismissOutside);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOutside);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, []);

  useEffect(() => {
    if (accountMenuRef.current) accountMenuRef.current.open = false;
  }, [pathname]);

  useEffect(() => {
    // The same outside-click behavior as the profile card. Native details
    // do not dismiss themselves when the user clicks elsewhere.
    const selector = "details.pb-organize-item-menu[open], details.pb-label-menu[open], details.pb-premium-move-menu[open]";
    const dismiss = (event: PointerEvent) => {
      if (!(event.target instanceof Node)) return;
      document.querySelectorAll<HTMLDetailsElement>(selector).forEach((menu) => {
        if (!menu.contains(event.target as Node)) menu.open = false;
      });
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      document.querySelectorAll<HTMLDetailsElement>(selector).forEach((menu) => {
        menu.open = false;
      });
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  const byRole = new Map(folders.map((folder) => [folder.role, folder]));
  const custom = folders.filter((folder) => !PINNED_ROLES.has(folder.role));
  const initials = accountInitials(mailbox.full_name, mailbox.email);
  const otherAccounts = savedAccounts.filter(
    (item) => item.mailbox.id !== mailbox.id,
  );

  const themeOptions = [
    { value: "light" as const, Icon: Sun, label: "Light" },
    { value: "system" as const, Icon: Monitor, label: "System" },
    { value: "dark" as const, Icon: Moon, label: "Dark" },
  ];

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        isEditableTarget(event.target)
      ) {
        return;
      }
      if (event.key.toLowerCase() === "c") {
        event.preventDefault();
        router.push("/postbox?compose=new");
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [router]);

  return (
    <div className={`pb pb-premium-shell ${
      preferences.density === "compact" ? "pb-density-compact" : ""
    }`}>
      {railOpen && <button type="button" className="pb-premium-overlay" aria-label="Close folders" onClick={closeRail} />}

      <aside className="pb-premium-sidebar" data-open={railOpen ? "true" : "false"}>
        <Link
          href="/postbox?folder=INBOX"
          className="pb-premium-brand"
          aria-label="PostBox Inbox"
          onClick={() => {
            closeRail();
            window.dispatchEvent(new Event("postbox:return-to-list"));
          }}
        >
          <svg
            className="pb-premium-brand-mark"
            viewBox="0 0 64 64"
            aria-hidden="true"
          >
            <rect width="64" height="64" fill="#0B1F44" />
            <path
              fill="#FFFFFF"
              d="M13.5 27L13.5 50.5L50.5 50.5L50.5 27L45.5 27L45.5 45.5L18.5 45.5L18.5 27Z"
            />
            <path
              fill="#6FA8FF"
              d="M13.5 12L32 25.875L50.5 12L50.5 18.25L32 32.125L13.5 18.25Z"
            />
          </svg>
          <span className="pb-premium-wordmark">PostBox</span>
        </Link>

        <div className="pb-premium-compose-wrap">
          <Link href="/postbox?compose=new" className="pb-premium-compose" onClick={closeRail}>
            <PenLine className="h-[18px] w-[18px]" aria-hidden="true" />
            Compose
            <kbd>C</kbd>
          </Link>
        </div>

        <nav className="pb-premium-nav" aria-label="Folders">
          <Suspense fallback={null}>
            <PremiumFolderNavigation
              folders={folders}
              byRole={byRole}
              custom={custom}
              labels={labels}
              onNavigate={closeRail}
              onCreate={openEditor}
              onDelete={deleteEntry}
            />
          </Suspense>
        </nav>

      </aside>

      <main className="pb-premium-workspace">
        <header className="pb-premium-topbar">
          <button
            type="button"
            className="pb-premium-mobile-trigger"
            aria-label="Open folders"
            onClick={() => setRailOpen(true)}
          >
            <Menu className="h-5 w-5" aria-hidden="true" />
          </button>

          <Suspense fallback={<div className="pb-premium-search" aria-hidden="true" />}>
            <PremiumSearch />
          </Suspense>

          <div className="pb-premium-top-actions">
            <Link
              href="/postbox/settings?section=appearance"
              className="pb-premium-icon-link"
              aria-label="Appearance and layout"
              title="Appearance and layout"
            >
              <Settings2 className="h-5 w-5" aria-hidden="true" />
            </Link>

            <details className="pb-premium-account" ref={accountMenuRef}>
              <summary aria-label="Account menu">
                <span className="pb-premium-avatar" aria-hidden="true">{initials || "PB"}</span>
                <ChevronDown className="h-3.5 w-3.5 pb-muted" aria-hidden="true" />
              </summary>
              <div className="pb-premium-account-panel">
                <div className="pb-premium-account-current">
                  <span className="pb-premium-account-avatar-lg" aria-hidden="true">
                    {initials || "PB"}
                  </span>
                  <div className="pb-premium-account-copy">
                    <strong>{mailbox.full_name || mailbox.email}</strong>
                    <span>{mailbox.email}</span>
                  </div>
                  <Link
                    href="/postbox/settings?section=account"
                    className="pb-premium-account-manage"
                  >
                    <User className="h-4 w-4" aria-hidden="true" />
                    Manage account
                  </Link>
                </div>

                {otherAccounts.length > 0 && (
                  <div className="pb-premium-account-switcher">
                    <div className="pb-premium-account-section-label">
                      Accounts on this device
                    </div>
                    {otherAccounts.map((item) => {
                      const itemInitials = accountInitials(
                        item.mailbox.full_name,
                        item.mailbox.email,
                      );
                      const switching = switchingAccount === item.session_id;
                      return (
                        <button
                          key={item.session_id}
                          type="button"
                          className="pb-premium-account-row"
                          disabled={Boolean(switchingAccount)}
                          onClick={async () => {
                            setAccountMenuError(null);
                            setSwitchingAccount(item.session_id);
                            try {
                              await postbox.switchAccount(item.session_id);
                              window.location.assign("/postbox?folder=INBOX");
                            } catch {
                              setAccountMenuError(
                                "This account needs to be signed in again.",
                              );
                              setSavedAccounts((current) =>
                                current.filter(
                                  (saved) => saved.session_id !== item.session_id,
                                ),
                              );
                              setSwitchingAccount(null);
                            }
                          }}
                        >
                          <span className="pb-premium-account-avatar-sm" aria-hidden="true">
                            {itemInitials || "PB"}
                          </span>
                          <span className="pb-premium-account-row-copy">
                            <strong>
                              {item.mailbox.full_name || item.mailbox.email}
                            </strong>
                            <small>{item.mailbox.email}</small>
                          </span>
                          {switching ? (
                            <Loader2
                              className="h-4 w-4 animate-spin pb-muted"
                              aria-hidden="true"
                            />
                          ) : (
                            <span className="pb-premium-account-switch-label">
                              Switch
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}

                <Link
                  href="/postbox/add-account"
                  className="pb-premium-account-add"
                >
                  <UserPlus className="h-4 w-4" aria-hidden="true" />
                  Add another account
                </Link>

                <div className="pb-premium-account-appearance">
                  <span className="pb-premium-account-appearance-label">Theme</span>
                  <div className="pb-premium-theme-row" role="group" aria-label="Colour theme">
                    {themeOptions.map(({ value, Icon, label }) => (
                      <button
                        key={value}
                        type="button"
                        className="pb-premium-theme"
                        aria-label={`${label} theme`}
                        title={`${label} theme`}
                        aria-pressed={preferences.theme === value}
                        onClick={() => void updatePreferences({ theme: value })}
                      >
                        <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    ))}
                  </div>
                </div>

                {accountMenuError && (
                  <p className="pb-premium-account-error" role="alert">
                    {accountMenuError}
                  </p>
                )}

                <Link
                  href="/postbox/contacts"
                  className="pb-premium-account-contacts"
                >
                  <Users className="h-4 w-4" aria-hidden="true" />
                  Contacts
                </Link>

                <div className="pb-premium-account-footer">
                  <Link href="/postbox/settings">
                    <Settings className="h-4 w-4" aria-hidden="true" />
                    Settings
                  </Link>
                  <button
                    type="button"
                    onClick={async () => {
                      await signOut();
                      router.replace("/postbox/login");
                    }}
                  >
                    <LogOut className="h-4 w-4" aria-hidden="true" />
                    Sign out
                  </button>
                  {savedAccounts.length > 1 && (
                    <button
                      type="button"
                      onClick={async () => {
                        await postbox.logoutDevice();
                        window.location.assign("/postbox/login");
                      }}
                    >
                      <LogOut className="h-4 w-4" aria-hidden="true" />
                      Sign out all
                    </button>
                  )}
                </div>
              </div>
            </details>
          </div>
        </header>

        <div className="pb-premium-content">{children}</div>
      </main>
      {editor && (
        <div className="pb-organize-dialog-backdrop" onPointerDown={(event) => {
          if (event.target === event.currentTarget && !saving) setEditor(null);
        }}>
          <form className="pb-organize-dialog" onSubmit={(event) => void saveEntry(event)}
            role="dialog" aria-modal="true" aria-labelledby="pb-organize-heading">
            <h2 id="pb-organize-heading">
              {editor.colorOnly ? "Change " : editor.original ? "Edit " : "Create "}
              {editor.kind === "folder" ? "Folder" : "Label"}
              {editor.colorOnly ? " Color" : ""}
            </h2>
            {!editor.colorOnly && (
              <>
                <label htmlFor="pb-organize-name">Name</label>
                <input id="pb-organize-name" autoFocus maxLength={editor.kind === "label" ? 80 : 200}
                  value={entryName} onChange={(event) => setEntryName(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape" && !saving) setEditor(null);
                  }}
                  placeholder={editor.kind === "folder" ? "e.g. Finance" : "e.g. Important"}
                  required />
              </>
            )}
            <fieldset className="pb-organize-color-field">
              <legend>Color</legend>
              <div className="pb-organize-swatches">
                {ORGANIZE_COLORS.map(([hex, label]) => (
                  <button key={hex} type="button" title={label} aria-label={label + " color"}
                    aria-pressed={entryColor === hex} className="pb-organize-color-swatch"
                    style={{ backgroundColor: hex }}
                    onClick={() => setEntryColor(hex)}>
                    {entryColor === hex && <span aria-hidden="true">✓</span>}
                  </button>
                ))}
                <label className="pb-organize-custom-color" title="Custom color">
                  <span aria-hidden="true" style={{ backgroundColor: entryColor }} />
                  <input type="color" aria-label="Pick a custom color" value={entryColor}
                    onChange={(event) => setEntryColor(event.target.value)} />
                  Custom
                </label>
              </div>
            </fieldset>
            {editorError && <p className="pb-organize-error" role="alert">{editorError}</p>}
            <div className="pb-organize-dialog-actions">
              <button type="button" disabled={saving} onClick={() => setEditor(null)}>Cancel</button>
              <button type="submit" disabled={saving || !entryName.trim()}>
                {saving ? "Saving…" : editor.colorOnly ? "Apply" : editor.original ? "Save" : "Create"}
              </button>
            </div>
          </form>
        </div>
      )}
      {deleteDialog && (
        <div className="pb-organize-dialog-backdrop" onPointerDown={(event) => {
          if (event.target === event.currentTarget && !deletingFolder) setDeleteDialog(null);
        }}>
          <section className="pb-organize-dialog pb-folder-delete-dialog" role="alertdialog"
            aria-modal="true" aria-labelledby="pb-folder-delete-title"
            aria-describedby="pb-folder-delete-description"
            onKeyDown={(event) => {
              if (event.key === "Escape" && !deletingFolder) setDeleteDialog(null);
            }}>
            <div className="pb-folder-delete-heading">
              <ShieldAlert size={22} aria-hidden="true" />
              <h2 id="pb-folder-delete-title">
                {deleteDialog.kind === "checking" ? "Checking folder…" :
                  deleteDialog.messageCount ? "Folder Contains Emails" :
                  deleteDialog.activeRules ? "Folder Has Active Rules" :
                  deleteDialog.kind === "error" ? "Folder Unavailable" : "Delete Folder?"}
              </h2>
            </div>
            <div id="pb-folder-delete-description">
              <p className="pb-folder-delete-name">{deleteDialog.name}</p>
              {deleteDialog.kind === "checking" ? (
                <p>Checking current emails and mail rules…</p>
              ) : deleteDialog.messageCount > 0 ? (
                <p>This folder contains {deleteDialog.messageCount} email{deleteDialog.messageCount === 1 ? "" : "s"} and cannot be deleted.
                  Please manually move all emails to your Inbox or another folder first.</p>
              ) : deleteDialog.activeRules > 0 ? (
                <p>{deleteDialog.activeRules} active Mail Rule{deleteDialog.activeRules === 1 ? "" : "s"} use this folder.
                  Disable or retarget {deleteDialog.activeRules === 1 ? "it" : "them"} in Settings before deleting.</p>
              ) : deleteDialog.kind === "error" ? (
                <p>We could not safely check this folder. Nothing was deleted.</p>
              ) : (
                <p>This folder is empty and has no active rules. Delete it? This will not delete any emails.</p>
              )}
              {deleteDialog.error && <p className="pb-organize-error" role="status">{deleteDialog.error}</p>}
            </div>
            <div className="pb-organize-dialog-actions">
              <button type="button" disabled={deletingFolder} onClick={() => setDeleteDialog(null)}>Cancel</button>
              {deleteDialog.kind === "ready" && deleteDialog.messageCount > 0 && (
                <button type="button" className="pb-folder-delete-primary"
                  onClick={() => {
                    const name = deleteDialog.name;
                    setDeleteDialog(null);
                    router.push("/postbox?folder=" + encodeURIComponent(name));
                  }}>Open Folder</button>
              )}
              {deleteDialog.kind === "ready" && !deleteDialog.messageCount && deleteDialog.activeRules > 0 && (
                <button type="button" className="pb-folder-delete-primary"
                  onClick={() => { setDeleteDialog(null); router.push("/postbox/settings?section=rules"); }}>
                  Go to Rules
                </button>
              )}
              {deleteDialog.kind === "ready" && !deleteDialog.messageCount && !deleteDialog.activeRules && (
                <button type="button" className="pb-folder-delete-danger" disabled={deletingFolder}
                  onClick={() => void confirmFolderDelete()}>
                  {deletingFolder ? "Deleting…" : "Delete Folder"}
                </button>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function PremiumSearch() {
  const params = useSearchParams();
  const urlQuery = params.get("q") || "";
  return (
    <PremiumSearchForm
      key={urlQuery}
      initialValue={urlQuery}
      paramsText={params.toString()}
    />
  );
}

function PremiumSearchForm({
  initialValue,
  paramsText,
}: {
  initialValue: string;
  paramsText: string;
}) {
  const router = useRouter();
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey ||
        event.key !== "/" ||
        isEditableTarget(event.target)
      ) {
        return;
      }
      event.preventDefault();
      inputRef.current?.focus();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <form
      className="pb-premium-search"
      role="search"
      onSubmit={(event) => {
        event.preventDefault();
        const next = new URLSearchParams(paramsText);
        const clean = value.trim();
        if (clean) {
          next.set("q", clean);
          next.set("scope", "all");
        } else {
          next.delete("q");
          next.delete("scope");
        }
        next.delete("page");
        router.push(`/postbox?${next.toString()}`);
      }}
    >
      <Search className="h-[19px] w-[19px]" aria-hidden="true" />
      <input
        ref={inputRef}
        type="search"
        aria-label="Search mail"
        placeholder="Search your mail"
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      {value ? (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setValue("");
            const next = new URLSearchParams(paramsText);
            next.delete("q");
            next.delete("scope");
            next.delete("page");
            router.push(`/postbox?${next.toString()}`);
          }}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      ) : (
        <kbd>/</kbd>
      )}
    </form>
  );
}

function accountInitials(name: string, email: string): string {
  return (name || email)
    .split(/\s+|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable="true"], [role="textbox"]',
    ),
  );
}

function PremiumFolderNavigation({
  byRole, custom, labels, onNavigate, onCreate, onDelete,
}: {
  folders: Folder[];
  byRole: Map<string, Folder>;
  custom: Folder[];
  labels: MailLabel[];
  onNavigate: () => void;
  onCreate: (
    kind: "folder" | "label", name?: string, id?: string,
    color?: string | null, colorOnly?: boolean,
  ) => void;
  onDelete: (kind: "folder" | "label", name: string, id?: string) => void;
}) {
  const pathname = usePathname();
  const params = useSearchParams();
  const selectedFolder = params.get("folder") || "INBOX";
  const selectedLabel = params.get("label");
  const starred = params.get("starred") === "true";
  const [foldersOpen, setFoldersOpen] = useState(true);
  const [labelsOpen, setLabelsOpen] = useState(true);

  return (
    <>
      {PINNED.map(({ role, label, icon: Icon, href }) => {
        const folder = byRole.get(role);
        if (!href && !folder) return null;
        const target = href ?? "/postbox?folder=" + encodeURIComponent(folder!.name);
        const active = role === "starred"
          ? pathname === "/postbox" && starred && !selectedLabel
          : pathname === "/postbox" && !starred && !selectedLabel && folder?.name === selectedFolder;
        const count = role === "drafts" || role === "scheduled"
          ? folder?.messages : role === "starred" ? undefined : folder?.unseen;
        return (
          <Link key={role} href={target} className="pb-premium-nav-link"
            data-role={role} aria-current={active ? "page" : undefined}
            onClick={() => {
              onNavigate();
              window.dispatchEvent(new Event("postbox:return-to-list"));
            }}>
            <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
            <span className="truncate">{label}</span>
            {count ? <span className="pb-premium-nav-count">{count}</span> : null}
          </Link>
        );
      })}

      <div className="pb-organize-section-header">
        <button type="button" aria-expanded={foldersOpen}
          aria-label={foldersOpen ? "Collapse folders" : "Expand folders"}
          onClick={() => setFoldersOpen(!foldersOpen)}>
          {foldersOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <span>FOLDERS</span>
        </button>
        <button type="button" aria-label="Create folder" title="Create folder"
          onClick={() => onCreate("folder")}><Plus size={17} /></button>
      </div>
      {foldersOpen && custom.map((folder) => {
        const active = pathname === "/postbox" && !selectedLabel &&
          !starred && folder.name === selectedFolder;
        return (
          <div className="pb-organize-nav-row" key={folder.name}>
            <Link href={"/postbox?folder=" + encodeURIComponent(folder.name)}
              className="pb-premium-nav-link" aria-current={active ? "page" : undefined}
              onClick={() => {
                onNavigate();
                window.dispatchEvent(new Event("postbox:return-to-list"));
              }}>
              <FolderIcon size={17} aria-hidden="true" style={{ color: folder.color || FOLDER_COLOR_DEFAULT }} />
              <span className="truncate">{folder.name}</span>
              {folder.unseen ? <span className="pb-premium-nav-count">{folder.unseen}</span> : null}
            </Link>
            <details className="pb-organize-item-menu">
              <summary aria-label={"Manage folder " + folder.name}
                title={"Manage folder " + folder.name}><MoreHorizontal size={17} /></summary>
              <div>
                <button type="button" onClick={(event) => {
                  event.currentTarget.closest("details")!.open = false;
                  onCreate("folder", folder.name, undefined, folder.color);
                }}>Edit</button>
                <button type="button" onClick={(event) => {
                  event.currentTarget.closest("details")!.open = false;
                  onCreate("folder", folder.name, undefined, folder.color, true);
                }}>Change Color</button>
                <button type="button" onClick={(event) => {
                  event.currentTarget.closest("details")!.open = false;
                  onDelete("folder", folder.name);
                }}>Delete</button>
              </div>
            </details>
          </div>
        );
      })}

      <div className="pb-organize-section-header">
        <button type="button" aria-expanded={labelsOpen}
          aria-label={labelsOpen ? "Collapse labels" : "Expand labels"}
          onClick={() => setLabelsOpen(!labelsOpen)}>
          {labelsOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <span>LABELS</span>
        </button>
        <button type="button" aria-label="Create label" title="Create label"
          onClick={() => onCreate("label")}><Plus size={17} /></button>
      </div>
      {labelsOpen && labels.map((label) => (
        <div className="pb-organize-nav-row" key={label.id}>
          <Link href={"/postbox?label=" + label.id} className="pb-premium-nav-link"
            aria-current={selectedLabel === label.id ? "page" : undefined}
            onClick={() => {
              onNavigate();
              window.dispatchEvent(new Event("postbox:return-to-list"));
            }}>
            <Tag size={17} aria-hidden="true" style={{ color: label.color || LABEL_COLOR_DEFAULT }} />
            <span className="truncate">{label.name}</span>
          </Link>
          <details className="pb-organize-item-menu">
            <summary aria-label={"Manage label " + label.name}
              title={"Manage label " + label.name}><MoreHorizontal size={17} /></summary>
            <div>
              <button type="button" onClick={(event) => {
                event.currentTarget.closest("details")!.open = false;
                onCreate("label", label.name, label.id, label.color);
              }}>Edit</button>
              <button type="button" onClick={(event) => {
                event.currentTarget.closest("details")!.open = false;
                onCreate("label", label.name, label.id, label.color, true);
              }}>Change Color</button>
              <button type="button" onClick={(event) => {
                event.currentTarget.closest("details")!.open = false;
                onDelete("label", label.name, label.id);
              }}>Delete</button>
            </div>
          </details>
        </div>
      ))}

    </>
  );
}
