"use client";

/**
 * The Platform Console shell: navigation, header, global search, auth guard.
 *
 * The guard here is a convenience, not the security boundary. Every
 * `/api/platform/` endpoint enforces `IsPlatformAdmin` server-side; this
 * exists so a customer who somehow reaches the URL sees a redirect instead of
 * a console frame full of 403s.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Building2,
  ClipboardList,
  Clock,
  Database,
  FileText,
  Globe2,
  HeartPulse,
  Inbox,
  Layers,
  LogOut,
  Mail,
  Menu,
  Search,
  Send,
  ShieldAlert,
  X,
} from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { usePlatformAuth } from "@/contexts/platform-auth-context";
import { platformApi, type SearchHit } from "@/lib/platform-api";
import { ThemeToggle } from "./theme";
import { Spinner, useDebounced } from "./ui";

interface NavItem {
  href: string;
  label: string;
  icon: typeof Activity;
}

interface NavSection {
  title?: string;
  items: NavItem[];
}

/**
 * The information architecture, and nothing beyond it.
 *
 * Every entry loads real data from an endpoint that exists. There is no
 * "coming soon" page: a capability MateMail does not have yet is not a menu
 * item, because a menu item is a promise.
 */
const NAV: NavSection[] = [
  { items: [{ href: "/platform", label: "Overview", icon: Activity }] },
  {
    title: "Organizations",
    items: [
      { href: "/platform/organizations", label: "All Organizations", icon: Building2 },
      { href: "/platform/pending", label: "Pending Approvals", icon: Clock },
    ],
  },
  {
    title: "Mail Infrastructure",
    items: [
      { href: "/platform/domains", label: "Domains", icon: Globe2 },
      { href: "/platform/mailboxes", label: "Mailboxes", icon: Mail },
      { href: "/platform/routing", label: "Aliases & Forwarding", icon: Send },
    ],
  },
  {
    title: "Mail Operations",
    items: [
      { href: "/platform/queue", label: "Queue", icon: Inbox },
      { href: "/platform/quarantine", label: "Quarantine", icon: ShieldAlert },
      { href: "/platform/logs", label: "Mail Logs", icon: FileText },
    ],
  },
  {
    title: "Platform",
    items: [
      { href: "/platform/plans", label: "Plans & Limits", icon: Layers },
      { href: "/platform/audit", label: "Audit Logs", icon: ClipboardList },
      { href: "/platform/health", label: "System Health", icon: HeartPulse },
      { href: "/platform/backups", label: "Backups", icon: Database },
    ],
  },
];

function isCurrent(pathname: string, href: string): boolean {
  if (href === "/platform") return pathname === "/platform";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function PlatformShell({ children }: { children: React.ReactNode }) {
  const { user, isLoading, signOut } = usePlatformAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    if (!isLoading && !user) router.replace("/platform/login");
  }, [isLoading, user, router]);

  // Closing the drawer is what the click means, so it happens in the click.
  // Doing it in an effect keyed on the pathname was a synchronous setState in
  // an effect body — flagged by react-hooks, and a roundabout way of
  // expressing "this button closes the menu".
  const closeNav = useCallback(() => setNavOpen(false), []);

  if (isLoading) {
    return (
      <div className="pf flex min-h-screen items-center justify-center">
        <Spinner label="Checking your session" />
      </div>
    );
  }

  if (!user) {
    // The effect above is redirecting. Rendering the shell here would flash a
    // console frame at somebody who is not entitled to see one.
    return <div className="pf min-h-screen" />;
  }

  return (
    <div className="pf min-h-screen">
      {/* Mobile drawer backdrop */}
      {navOpen && (
        <div
          className="fixed inset-0 z-30 md:hidden"
          style={{ background: "rgb(2 6 23 / 0.5)" }}
          onClick={() => setNavOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r transition-transform md:translate-x-0 ${
          navOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        style={{
          background: "var(--pf-surface)",
          borderColor: "var(--pf-border)",
        }}
      >
        <div
          className="flex h-14 items-center gap-2.5 border-b px-4"
          style={{ borderColor: "var(--pf-border)" }}
        >
          <BrandMark size={30} preload />
          <div className="min-w-0">
            <p
              className="truncate text-sm font-bold leading-tight"
              style={{ color: "var(--pf-text)" }}
            >
              MateMail
            </p>
            <p className="pf-label" style={{ letterSpacing: "0.08em" }}>
              Platform Console
            </p>
          </div>
          <button
            type="button"
            className="pf-btn pf-btn-ghost ml-auto md:hidden"
            style={{ padding: "0.2rem 0.35rem" }}
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto p-2.5" aria-label="Platform Console">
          {NAV.map((section, index) => (
            <div key={section.title ?? index} className={index > 0 ? "mt-4" : ""}>
              {section.title && (
                <p className="pf-label mb-1.5 px-2">{section.title}</p>
              )}
              <ul className="space-y-0.5">
                {section.items.map(({ href, label, icon: Icon }) => (
                  <li key={href}>
                    <Link
                      href={href}
                      className="pf-nav-item"
                      onClick={closeNav}
                      aria-current={isCurrent(pathname, href) ? "page" : undefined}
                    >
                      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                      <span className="truncate">{label}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div
          className="border-t p-2.5 text-xs"
          style={{ borderColor: "var(--pf-border)" }}
        >
          <p className="truncate font-medium" style={{ color: "var(--pf-text)" }}>
            {user.full_name || user.email}
          </p>
          <p className="truncate pf-faint">{user.email}</p>
          <button
            type="button"
            className="pf-btn pf-btn-ghost mt-2 w-full"
            onClick={async () => {
              await signOut();
              router.replace("/platform/login");
            }}
          >
            <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>

      <div className="md:pl-60">
        <header
          className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b px-4"
          style={{
            background: "var(--pf-surface)",
            borderColor: "var(--pf-border)",
          }}
        >
          <button
            type="button"
            className="pf-btn pf-btn-ghost md:hidden"
            style={{ padding: "0.3rem 0.4rem" }}
            aria-label="Open navigation"
            onClick={() => setNavOpen(true)}
          >
            <Menu className="h-4 w-4" aria-hidden="true" />
          </button>

          <GlobalSearch />

          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1400px] p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}

/**
 * One box for the five things an incident starts from.
 *
 * Backed by a single endpoint doing indexed `icontains` against five tables.
 * It is a jump list, not a search product — an operator pastes an address, a
 * domain or a UUID out of a log line and wants the right page.
 */
function GlobalSearch() {
  const [term, setTerm] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const debounced = useDebounced(term, 250);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();

  // A term that is too short has no results by definition, so that case is
  // derived below rather than written into state from an effect.
  useEffect(() => {
    let cancelled = false;
    if (debounced.trim().length < 2) return;
    platformApi
      .search(debounced.trim())
      .then((data) => {
        if (!cancelled) setHits(data.results);
      })
      .catch(() => {
        if (!cancelled) setHits([]);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Derived, so a stale list never shows under a query that is too short.
  const visibleHits = term.trim().length < 2 ? [] : hits;

  const go = useCallback(
    (href: string) => {
      setOpen(false);
      setTerm("");
      router.push(href);
    },
    [router],
  );

  return (
    <div ref={containerRef} className="relative min-w-0 flex-1 sm:max-w-md">
      <Search
        className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2"
        style={{ color: "var(--pf-text-faint)" }}
        aria-hidden="true"
      />
      <input
        id="pf-global-search"
        className="pf-input"
        style={{ paddingLeft: "1.9rem" }}
        placeholder="Search organizations, domains, mailboxes…"
        aria-label="Global search"
        value={term}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          setTerm(event.target.value);
          setOpen(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "Enter" && visibleHits.length > 0) go(visibleHits[0].href);
        }}
      />

      {open && term.trim().length >= 2 && (
        <div
          className="pf-card absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto p-1"
          role="listbox"
        >
          {visibleHits.length === 0 ? (
            <p className="px-3 py-3 text-xs pf-muted">No matches.</p>
          ) : (
            visibleHits.map((hit) => (
              <button
                key={`${hit.type}-${hit.id}`}
                type="button"
                role="option"
                aria-selected="false"
                className="pf-nav-item w-full text-left"
                onClick={() => go(hit.href)}
              >
                <span className="pf-label w-20 shrink-0">{hit.type}</span>
                <span className="min-w-0 flex-1 truncate" style={{ color: "var(--pf-text)" }}>
                  {hit.label}
                </span>
                <span className="truncate text-xs pf-faint">{hit.sublabel}</span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
