"use client";

/**
 * The PostBox shell: folder rail, header, and the guard.
 *
 * The guard is a convenience. Every `/api/postbox/` endpoint is authenticated
 * by the session cookie server-side; this stops somebody seeing a mail client
 * frame full of failed requests before the redirect lands.
 */
import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Archive,
  Clock,
  FileText,
  Inbox,
  Loader2,
  LogOut,
  Menu,
  Moon,
  Monitor,
  Send,
  Settings,
  ShieldAlert,
  Star,
  Sun,
  Trash2,
  Users,
  X,
} from "lucide-react";

import { NetaMateBrand } from "@/components/netamate-brand";
import { usePostBox } from "@/contexts/postbox-context";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";
import { postbox, type Folder } from "@/lib/postbox-api";

/** Folder roles PostBox pins to the top, in the order people expect them. */
const PINNED: Array<{ role: string; label: string; icon: typeof Inbox }> = [
  { role: "inbox", label: "Inbox", icon: Inbox },
  { role: "drafts", label: "Drafts", icon: FileText },
  { role: "sent", label: "Sent", icon: Send },
  { role: "archive", label: "Archive", icon: Archive },
  { role: "junk", label: "Spam", icon: ShieldAlert },
  { role: "trash", label: "Trash", icon: Trash2 },
];

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
  const custom = folders.filter(
    (f) => !f.role && f.name.toUpperCase() !== "SCHEDULED",
  );
  const scheduled = folders.find((f) => f.name.toUpperCase() === "SCHEDULED");

  const themeOptions = [
    { value: "light" as const, Icon: Sun, label: "Light" },
    { value: "system" as const, Icon: Monitor, label: "System" },
    { value: "dark" as const, Icon: Moon, label: "Dark" },
  ];

  return (
    <div
      className={`pb flex min-h-screen ${IS_NETAMATE_EMAIL ? "nm-postbox " : ""}${
        preferences.density === "compact" ? "pb-density-compact" : ""
      }`}
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
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r transition-transform md:static md:translate-x-0 ${
          railOpen ? "translate-x-0" : "-translate-x-full"
        }`}
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
          <button
            type="button"
            className="pb-btn pb-btn-plain ml-auto md:hidden"
            aria-label="Close folders"
            onClick={closeRail}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <div className="p-3">
          <Link
            href="/postbox?compose=new"
            className="pb-btn pb-btn-primary pb-compose-cta w-full"
          >
            Compose
          </Link>
        </div>

        <nav className="pb-scroll flex-1 px-2 pb-3" aria-label="Folders">
          {PINNED.map(({ role, label, icon: Icon }) => {
            const folder = byRole.get(role);
            if (!folder) return null;
            return (
              <FolderLink
                key={role}
                href={`/postbox?folder=${encodeURIComponent(folder.name)}`}
                icon={Icon}
                label={label}
                unseen={role === "drafts" ? folder.messages : folder.unseen}
                onNavigate={closeRail}
              />
            );
          })}

          <FolderLink
            href="/postbox?folder=INBOX&starred=true"
            icon={Star}
            label="Starred"
            onNavigate={closeRail}
          />

          {scheduled && (
            <FolderLink
              href={`/postbox?folder=${encodeURIComponent(scheduled.name)}`}
              icon={Clock}
              label="Scheduled"
              unseen={scheduled.messages}
              onNavigate={closeRail}
            />
          )}

          {custom.length > 0 && (
            <>
              <p className="pb-label mt-4 px-2">Folders</p>
              {custom.map((folder) => (
                <FolderLink
                  key={folder.name}
                  href={`/postbox?folder=${encodeURIComponent(folder.name)}`}
                  icon={Archive}
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

      <div className="flex min-w-0 flex-1 flex-col">
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

        <div className="min-h-0 flex-1">{children}</div>
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
