"use client";

/**
 * The PostBox shell: folder rail, header, and the guard.
 *
 * The guard is a convenience. Every `/api/postbox/` endpoint is authenticated
 * by the session cookie server-side; this stops somebody seeing a mail client
 * frame full of failed requests before the redirect lands.
 */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  Archive,
  ChevronDown,
  Clock,
  FileText,
  Folder as FolderIcon,
  HardDrive,
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
  Trash2,
  Users,
  X,
} from "lucide-react";

import { NetaMateBrand } from "@/components/netamate-brand";
import { usePostBox } from "@/contexts/postbox-context";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";
import { postbox, type AccountInfo, type Folder } from "@/lib/postbox-api";

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

  const closeRail = useCallback(() => setRailOpen(false), []);

  if (isLoading) {
    return (
      <div className="pb flex min-h-screen items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
      </div>
    );
  }

  if (!mailbox) return <div className="pb min-h-screen" />;

  const byRole = new Map(folders.map((f) => [f.role, f]));
  // Anything the standard list does not claim. The backend now assigns a
  // role by canonical name when the server offers no special-use attribute,
  // so Sent/Drafts/Trash/Junk/Archive no longer land here — which is what
  // made them all render with the same icon.
  const custom = folders.filter((f) => !PINNED_ROLES.has(f.role));

  const themeOptions = [
    { value: "light" as const, Icon: Sun, label: "Light" },
    { value: "system" as const, Icon: Monitor, label: "System" },
    { value: "dark" as const, Icon: Moon, label: "Dark" },
  ];

  if (!IS_NETAMATE_EMAIL) {
    return (
      <PremiumPostBoxShell
        folders={folders}
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

  return (
    <div
      /*
        A viewport-sized APPLICATION, not a document that happens to be
        tall. `min-h-screen` was a minimum with no maximum, so a long
        message grew the shell, then the body, then the document — and the
        browser scrollbar became the reader's, carrying the sidebar and the
        message list down with it.

        `h-dvh` not `h-screen`: `100vh` excludes mobile browser chrome, so
        a `100vh` shell is taller than the visible area and the sidebar
        footer hides under the URL bar. `100dvh` tracks it.

        `overflow-hidden` is what stops content escaping into the document.
        It is here, on the authenticated shell, and deliberately NOT on
        `body` — which is shared with the public site, Workspace, MailAdmin,
        the Platform Console and every auth page, all of which must keep
        scrolling like normal pages.
      */
      className={`pb flex h-dvh min-h-0 overflow-hidden ${
        IS_NETAMATE_EMAIL ? "nm-postbox " : ""
      }${preferences.density === "compact" ? "pb-density-compact" : ""}`}
    >
      {railOpen && (
        <div
          className="fixed inset-0 z-30 md:hidden"
          style={{ background: "rgb(19 28 39 / 0.55)" }}
          onClick={closeRail}
          aria-hidden="true"
        />
      )}

      <aside
        // `nm-rail` narrows this to 13.5rem on the NetaMate surface only.
        // Applied by class rather than by swapping the width utility, so
        // the MateMail rail is untouched by a NetaMate design decision.
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r transition-transform md:static md:translate-x-0 ${
          IS_NETAMATE_EMAIL ? "nm-rail " : ""
        }${railOpen ? "translate-x-0" : "-translate-x-full"}`}
        style={{ background: "var(--pb-sidebar)", borderColor: "var(--pb-border)" }}
      >
        <div
          className="flex h-14 items-center gap-2.5 border-b px-4"
          style={{ borderColor: "var(--pb-border)" }}
        >
          {IS_NETAMATE_EMAIL ? (
            <NetaMateBrand surface="PostBox" compact preload />
          ) : (
            <>
              <Image src="/matemail-logo.png" alt="" width={26} height={19} priority />
              <div className="min-w-0">
                <p
                  className="pb-brand text-sm leading-none"
                  style={{ color: "var(--pb-primary)" }}
                >
                  MateMail
                </p>
                <p className="pb-label mt-0.5">PostBox</p>
              </div>
            </>
          )}
          {/*
            The mobile drawer's close control, and the wrapper is load-bearing.

            `md:hidden` on the button itself did NOT hide it. Tailwind v4
            emits utilities inside `@layer utilities`, and `.pb-btn` in
            globals.css is unlayered — unlayered CSS wins over every layer
            regardless of specificity or order, so `display:inline-flex`
            always beat `display:none` and the X appeared on desktop.

            The wrapper has no competing display rule, so the utility applies
            normally. Keep it.
          */}
          <div className="ml-auto md:hidden">
            <button
              type="button"
              className="pb-btn pb-btn-plain"
              aria-label="Close folders"
              onClick={closeRail}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="p-3">
          <Link
            href="/postbox?compose=new"
            className="pb-btn pb-btn-primary pb-compose-cta w-full"
          >
            Compose
          </Link>
        </div>

        {/*
          `min-h-0` is load-bearing. A flex item's default `min-height` is
          `auto`, so without it a long folder list makes this taller than
          its share instead of scrolling — pushing the account footer off
          the bottom of the sidebar rather than keeping it anchored.
        */}
        <nav className="pb-scroll min-h-0 flex-1 px-2 pb-3" aria-label="Folders">
          {PINNED.map(({ role, label, icon: Icon, href }) => {
            // Starred is a saved filter, not a mailbox, so it has an href
            // and no folder row behind it.
            if (href) {
              return (
                <FolderLink
                  key={role}
                  href={href}
                  icon={Icon}
                  label={label}
                  onNavigate={closeRail}
                />
              );
            }

            const folder = byRole.get(role);
            if (!folder) return null;
            return (
              <FolderLink
                key={role}
                href={`/postbox?folder=${encodeURIComponent(folder.name)}`}
                icon={Icon}
                label={label}
                // Drafts and Scheduled are counted by total, not unread:
                // nobody has 'unread drafts', and a zero there reads as
                // an empty folder when it is not.
                unseen={
                  role === "drafts" || role === "scheduled"
                    ? folder.messages
                    : folder.unseen
                }
                onNavigate={closeRail}
              />
            );
          })}

          {custom.length > 0 && (
            <>
              <p className="pb-label mt-4 px-2">Folders</p>
              {custom.map((folder) => (
                <FolderLink
                  key={folder.name}
                  href={`/postbox?folder=${encodeURIComponent(folder.name)}`}
                  // A folder somebody made is a folder. This was `Archive`,
                  // which gave every one of them the archive icon — and,
                  // while the standard folders were landing here too, gave
                  // Sent, Drafts, Trash and Junk that icon as well.
                  icon={FolderIcon}
                  label={folder.name}
                  unseen={folder.unseen}
                  onNavigate={closeRail}
                />
              ))}
            </>
          )}

          <p className="pb-label mt-4 px-2">{IS_NETAMATE_EMAIL ? "Tools" : "MateMail"}</p>
          <FolderLink
            href="/postbox/contacts"
            icon={Users}
            label="Contacts"
            onNavigate={closeRail}
          />
          <FolderLink
            href="/postbox/settings"
            icon={Settings}
            label="Settings"
            onNavigate={closeRail}
          />
        </nav>

        <div className="border-t p-3 text-xs" style={{ borderColor: "var(--pb-border)" }}>
          <p className="truncate font-medium">{mailbox.full_name || mailbox.email}</p>
          <p className="truncate pb-subtle">{mailbox.email}</p>

          <div
            className="mt-2 inline-flex items-center gap-0.5 p-0.5"
            role="radiogroup"
            aria-label="Colour theme"
            style={{ background: "var(--pb-surface-2)" }}
          >
            {themeOptions.map(({ value, Icon, label }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={preferences.theme === value}
                aria-label={label}
                title={label}
                className="pb-btn pb-btn-plain"
                style={{
                  padding: "0.25rem 0.4rem",
                  background:
                    preferences.theme === value ? "var(--pb-bg)" : "transparent",
                  color:
                    preferences.theme === value
                      ? "var(--pb-fg)"
                      : "var(--pb-subtle)",
                }}
                onClick={() => void updatePreferences({ theme: value })}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ))}
          </div>

          <button
            type="button"
            className="pb-btn pb-btn-ghost mt-2 w-full"
            onClick={async () => {
              await signOut();
              router.replace("/postbox/login");
            }}
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>

      {/*
        `min-h-0` for the same reason as the nav, and `overflow-hidden` so
        the route below can never make this column taller than the shell
        gave it.
      */}
      <div className="flex min-w-0 min-h-0 flex-1 flex-col overflow-hidden">
        <header
          className="flex h-14 shrink-0 items-center gap-3 border-b px-4 md:hidden"
          style={{ borderColor: "var(--pb-border)", background: "var(--pb-bg)" }}
        >
          <button
            type="button"
            className="pb-btn pb-btn-plain"
            aria-label="Open folders"
            onClick={() => setRailOpen(true)}
          >
            <Menu className="h-4 w-4" aria-hidden="true" />
          </button>
          {IS_NETAMATE_EMAIL ? (
            <NetaMateBrand surface="PostBox" compact />
          ) : (
            <p className="pb-brand text-sm" style={{ color: "var(--pb-primary)" }}>
              MateMail
            </p>
          )}
        </header>

        {/*
          The boundary every route's `h-full` resolves against. Bounded and
          overflow-hidden, so a page that scrolls does so INSIDE here —
          which is what makes `pb-scroll h-full` work on Contacts and
          Settings, and what gives the mailbox's list and reader something
          definite to divide.
        */}
        <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
      </div>
    </div>
  );
}

function FolderLink({
  href,
  icon: Icon,
  label,
  unseen,
  onNavigate,
}: {
  href: string;
  icon: typeof Inbox;
  label: string;
  unseen?: number;
  onNavigate: () => void;
}) {
  const pathname = usePathname();
  // Comparing the whole href would mark Inbox current on every query change.
  const current = href.startsWith("/postbox/") && pathname === href.split("?")[0];

  return (
    <Link
      href={href}
      className="pb-nav"
      aria-current={current ? "true" : undefined}
      onClick={onNavigate}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="truncate">{label}</span>
      {unseen ? <span className="pb-count">{unseen}</span> : null}
    </Link>
  );
}


function PremiumPostBoxShell({
  children,
  folders,
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
  mailbox: NonNullable<ReturnType<typeof usePostBox>["mailbox"]>;
  preferences: ReturnType<typeof usePostBox>["preferences"];
  railOpen: boolean;
  setRailOpen: (open: boolean) => void;
  closeRail: () => void;
  signOut: () => Promise<void>;
  updatePreferences: ReturnType<typeof usePostBox>["updatePreferences"];
}) {
  const router = useRouter();
  const [account, setAccount] = useState<AccountInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    postbox
      .account()
      .then((value) => {
        if (!cancelled) setAccount(value);
      })
      .catch(() => {
        if (!cancelled) setAccount(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const byRole = new Map(folders.map((folder) => [folder.role, folder]));
  const custom = folders.filter((folder) => !PINNED_ROLES.has(folder.role));
  const initials = (mailbox.full_name || mailbox.email)
    .split(/\s+|@/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");

  const storage = account?.storage;
  const storagePercent =
    storage?.available && typeof storage.percent === "number"
      ? Math.max(0, Math.min(100, storage.percent))
      : null;

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
        <div className="pb-premium-brand">
          <svg
            className="pb-premium-brand-mark"
            viewBox="0 0 64 64"
            role="img"
            aria-label="PostBox"
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
        </div>

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
              onNavigate={closeRail}
            />
          </Suspense>
        </nav>

        <div className="pb-premium-sidebar-footer">
          {storage?.available && storagePercent !== null ? (
            <Link href="/postbox/settings?section=account" className="pb-premium-storage" onClick={closeRail}>
              <span className="pb-premium-storage-head">
                <HardDrive className="h-4 w-4" aria-hidden="true" />
                Mailbox storage
              </span>
              <span className="pb-premium-storage-track" aria-hidden="true">
                <span className="pb-premium-storage-fill" style={{ width: `${storagePercent}%` }} />
              </span>
              <small>
                {typeof storage.used_mb === "number" ? `${storage.used_mb.toFixed(1)} MB used` : "Storage usage"}{typeof storage.quota_mb === "number" ? ` of ${storage.quota_mb.toFixed(0)} MB` : ""}
              </small>
            </Link>
          ) : (
            <Link href="/postbox/settings?section=account" className="pb-premium-storage" onClick={closeRail}>
              <span className="pb-premium-storage-head">
                <HardDrive className="h-4 w-4" aria-hidden="true" />
                Mailbox settings
              </span>
            </Link>
          )}

          <div className="pb-premium-theme-row" role="group" aria-label="Colour theme">
            {themeOptions.map(({ value, Icon, label }) => (
              <button
                key={value}
                type="button"
                className="pb-premium-theme"
                aria-label={label}
                aria-pressed={preferences.theme === value}
                onClick={() => void updatePreferences({ theme: value })}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            ))}
          </div>
        </div>
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

            <details className="pb-premium-account">
              <summary aria-label="Account menu">
                <span className="pb-premium-avatar" aria-hidden="true">{initials || "PB"}</span>
                <ChevronDown className="h-3.5 w-3.5 pb-muted" aria-hidden="true" />
              </summary>
              <div className="pb-premium-account-panel">
                <div className="pb-premium-account-copy">
                  <strong>{mailbox.full_name || mailbox.email}</strong>
                  <span>{mailbox.email}</span>
                </div>
                <Link href="/postbox/settings?section=account">
                  <User className="h-4 w-4" aria-hidden="true" />
                  Account
                </Link>
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
              </div>
            </details>
          </div>
        </header>

        <div className="pb-premium-content">{children}</div>
      </main>
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

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(
    target.closest(
      'input, textarea, select, [contenteditable="true"], [role="textbox"]',
    ),
  );
}

function PremiumFolderNavigation({
  byRole,
  custom,
  onNavigate,
}: {
  folders: Folder[];
  byRole: Map<string, Folder>;
  custom: Folder[];
  onNavigate: () => void;
}) {
  const pathname = usePathname();
  const params = useSearchParams();
  const selectedFolder = params.get("folder") || "INBOX";
  const starred = params.get("starred") === "true";

  return (
    <>
      {PINNED.map(({ role, label, icon: Icon, href }) => {
        const folder = byRole.get(role);
        if (!href && !folder) return null;

        const target =
          href ?? `/postbox?folder=${encodeURIComponent(folder!.name)}`;
        const active =
          role === "starred"
            ? pathname === "/postbox" && starred
            : pathname === "/postbox" && !starred && folder?.name === selectedFolder;
        const count =
          role === "drafts" || role === "scheduled"
            ? folder?.messages
            : role === "starred"
              ? undefined
              : folder?.unseen;

        return (
          <Link
            key={role}
            href={target}
            className="pb-premium-nav-link"
            data-role={role}
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
          >
            <Icon className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
            <span className="truncate">{label}</span>
            {count ? <span className="pb-premium-nav-count">{count}</span> : null}
          </Link>
        );
      })}

      {custom.length > 0 && (
        <>
          <div className="pb-premium-folder-label">YOUR FOLDERS</div>
          {custom.map((folder) => {
            const active =
              pathname === "/postbox" && !starred && folder.name === selectedFolder;
            return (
              <Link
                key={folder.name}
                href={`/postbox?folder=${encodeURIComponent(folder.name)}`}
                className="pb-premium-nav-link"
                aria-current={active ? "page" : undefined}
                onClick={onNavigate}
              >
                <FolderIcon className="h-[17px] w-[17px] shrink-0" aria-hidden="true" />
                <span className="truncate">{folder.name}</span>
                {folder.unseen ? <span className="pb-premium-nav-count">{folder.unseen}</span> : null}
              </Link>
            );
          })}
        </>
      )}

      <div className="pb-premium-separator" />
      <Link
        href="/postbox/contacts"
        className="pb-premium-nav-link"
        aria-current={pathname === "/postbox/contacts" ? "page" : undefined}
        onClick={onNavigate}
      >
        <Users className="h-[18px] w-[18px]" aria-hidden="true" />
        Contacts
      </Link>
      <Link
        href="/postbox/settings"
        className="pb-premium-nav-link"
        aria-current={pathname === "/postbox/settings" ? "page" : undefined}
        onClick={onNavigate}
      >
        <Settings className="h-[18px] w-[18px]" aria-hidden="true" />
        Settings
      </Link>
    </>
  );
}
