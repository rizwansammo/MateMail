"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  AtSign,
  ChevronDown,
  ChevronRight,
  CircleCheckBig,
  CreditCard,
  DatabaseBackup,
  ExternalLink,
  Globe2,
  KeyRound,
  LayoutDashboard,
  ListOrdered,
  ListPlus,
  UserCog,
  LogOut,
  Mail,
  Inbox,
  Menu,
  PlugZap,
  ScrollText,
  Send,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { WorkspaceThemeProvider, WorkspaceThemeToggle } from "@/components/workspace/theme";
import { useAuth } from "@/contexts/auth-context";
import { apiRequest } from "@/lib/api";

const navGroups = [
  {
    label: "Workspace",
    items: [
      { label: "Overview", href: "/app", icon: LayoutDashboard },
      { label: "Getting started", href: "/app/onboarding", icon: CircleCheckBig },
      { label: "Domains", href: "/app/domains", icon: Globe2 },
      { label: "Mailboxes", href: "/app/mailboxes", icon: Mail },
      { label: "TeamBoxes", href: "/app/team-boxes", icon: Inbox },
      { label: "Forward Groups", href: "/app/forward-groups", icon: ListPlus },
      { label: "Delegation", href: "/app/delegation", icon: UserCog },
      { label: "Aliases", href: "/app/aliases", icon: AtSign },
      { label: "Forwarding", href: "/app/forwarding", icon: Send },
    ],
  },
  {
    label: "Management",
    items: [
      { label: "Team", href: "/app/team", icon: Users },
      { label: "Billing & usage", href: "/app/billing", icon: CreditCard },
    ],
  },
  {
    label: "Operations",
    items: [
      { label: "Activity logs", href: "/app/logs", icon: ScrollText },
      { label: "Mail queue", href: "/app/queue", icon: ListOrdered },
      { label: "Quarantine", href: "/app/spam", icon: ShieldAlert },
      { label: "Backups", href: "/app/backups", icon: DatabaseBackup },
    ],
  },
  {
    label: "Configuration",
    items: [
      { label: "API keys", href: "/app/settings/api-keys", icon: KeyRound },
      { label: "Connected apps", href: "/app/settings/integrations", icon: PlugZap },
      { label: "Settings", href: "/app/settings", icon: Settings2 },
    ],
  },
];

const allNavItems = navGroups.flatMap((group) => group.items);
const auxiliaryNavItems = [
  { label: "Account security", href: "/app/security", icon: ShieldCheck },
  { label: "Authorize Connected App", href: "/app/integrations/authorize", icon: PlugZap },
];

function initials(value?: string | null) {
  return (value || "?")
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function displayPlan(value?: string | null) {
  if (!value) return "Workspace";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function roleLabel(value?: string | null) {
  if (!value) return "Member";
  return value
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, user, tenant, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [workspaceMeta, setWorkspaceMeta] = useState<{ id?: string; plan?: string; my_role?: string } | null>(null);

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      if (pathname.startsWith("/app/integrations/authorize")) {
        const query = typeof window !== "undefined" ? window.location.search : "";
        router.push("/login?next=" + encodeURIComponent(pathname + query));
      } else {
        router.push("/login");
      }
    }
  }, [isLoading, isAuthenticated, pathname, router]);

  useEffect(() => {
    if (!tenant?.id) return;
    let cancelled = false;
    apiRequest(`/api/workspaces/${tenant.id}/`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled) setWorkspaceMeta(data);
      })
      .catch(() => {
        if (!cancelled) setWorkspaceMeta(null);
      });
    return () => {
      cancelled = true;
    };
  }, [tenant?.id]);

  const activeItem = useMemo(() => {
    return [...allNavItems, ...auxiliaryNavItems]
      .filter(({ href }) =>
        href === "/app"
          ? pathname === href
          : pathname === href || pathname.startsWith(href + "/")
      )
      .sort((a, b) => b.href.length - a.href.length)[0];
  }, [pathname]);

  if (isLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-300 border-t-slate-950" />
      </div>
    );
  }

  if (!isAuthenticated) return null;

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const shellMeta = workspaceMeta?.id === tenant?.id ? workspaceMeta : null;
  const workspaceName = tenant?.name || "MateMail";
  const accountName = user?.full_name || user?.email || "Account";

  const accountMenu = (placement: "sidebar" | "topbar") => (
    <div className={"portal-account-menu " + (placement === "topbar" ? "portal-account-menu-top" : "")}>
      <div className="portal-account-menu-head">
        <strong>{accountName}</strong>
        <span>{user?.email}</span>
      </div>
      <Link className="portal-account-action" href="/app/settings">
        <Settings2 className="h-4 w-4" />
        Workspace settings
      </Link>
      <Link className="portal-account-action" href="/app/security">
        <ShieldCheck className="h-4 w-4" />
        Account security
      </Link>
      <Link className="portal-account-action" href="/app/settings/api-keys">
        <KeyRound className="h-4 w-4" />
        API keys
      </Link>
      <Link className="portal-account-action" href="/app/settings/integrations">
        <PlugZap className="h-4 w-4" />
        Connected apps
      </Link>
      {user?.is_platform_admin && (
        <Link className="portal-account-action" href="/admin">
          <ExternalLink className="h-4 w-4" />
          Admin console
        </Link>
      )}
      <div className="portal-theme-wrap">
        <span>Appearance</span>
        <WorkspaceThemeToggle />
      </div>
      <button className="portal-account-action danger" type="button" onClick={handleLogout}>
        <LogOut className="h-4 w-4" />
        Sign out
      </button>
    </div>
  );

  return (
    <WorkspaceThemeProvider>
      <div className="ws portal-premium portal-shell">
        <button
          className="portal-overlay"
          data-open={mobileOpen}
          aria-label="Close navigation"
          onClick={() => setMobileOpen(false)}
        />

        <aside className="portal-sidebar" data-open={mobileOpen}>
          <div className="portal-brand">
            <div className="portal-brand-lockup">
              <BrandMark size={31} className="portal-brand-mark" preload />
              <span className="portal-brand-word">MateMail Hub</span>
            </div>
          </div>
          <nav className="portal-nav" aria-label="Workspace navigation">
            {navGroups.map((group) => (
              <section className="portal-nav-group" key={group.label}>
                <span className="portal-nav-label">{group.label}</span>
                <div className="portal-nav-list">
                  {group.items.map(({ label, href, icon: Icon }) => (
                    <Link
                      key={href}
                      href={href}
                      className="portal-nav-item"
                      data-active={activeItem?.href === href}
                      aria-current={activeItem?.href === href ? "page" : undefined}
                      onClick={() => setMobileOpen(false)}
                    >
                      <Icon aria-hidden="true" />
                      <span>{label}</span>
                    </Link>
                  ))}
                </div>
              </section>
            ))}
          </nav>

          <div className="portal-sidebar-footer">
            {tenant && (
              <div className="portal-plan-mini">
                <div className="portal-plan-mini-top">
                  <Sparkles className="h-3.5 w-3.5 text-[var(--portal-primary)]" />
                  <strong>{shellMeta?.plan ? displayPlan(shellMeta.plan) : "Workspace"}</strong>
                  <span>{tenant.status === "active" ? "Active" : displayPlan(tenant.status)}</span>
                </div>
              </div>
            )}

            <details className="portal-account">
              <summary>
                <span className="portal-avatar">{initials(accountName)}</span>
                <span className="portal-account-copy">
                  <strong>{accountName}</strong>
                  <small>{roleLabel(shellMeta?.my_role || tenant?.role)}</small>
                </span>
                <ChevronDown className="h-3.5 w-3.5 text-[var(--portal-faint)]" />
              </summary>
              {accountMenu("sidebar")}
            </details>
          </div>
        </aside>

        <div className="portal-main">
          <header className="portal-topbar">
            <div className="portal-breadcrumb">
              <button
                type="button"
                className="portal-icon-button portal-mobile-trigger"
                aria-label="Open navigation"
                onClick={() => setMobileOpen(true)}
              >
                <Menu className="h-4 w-4" />
              </button>
              <span>{workspaceName}</span>
              <ChevronRight className="h-3.5 w-3.5" />
              <strong>{activeItem?.label || "Workspace"}</strong>
            </div>

            <div className="portal-top-actions">
              <details className="portal-account">
                <summary className="portal-icon-button" aria-label="Account menu">
                  <span className="portal-avatar">{initials(accountName)}</span>
                </summary>
                {accountMenu("topbar")}
              </details>
            </div>
          </header>

          <main className="portal-stage">{children}</main>

          <footer className="portal-footer">
            <span>MateMail Hub</span>
            <span>Secure administration for your email organization</span>
          </footer>
        </div>
      </div>
    </WorkspaceThemeProvider>
  );
}
