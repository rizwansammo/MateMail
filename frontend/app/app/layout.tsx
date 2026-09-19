"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Archive,
  Clock,
  CreditCard,
  DatabaseBackup,
  ExternalLink,
  FileText,
  Globe2,
  Layers,
  LogOut,
  Send,
  Settings,
  Shield,
  Users,
  UserPlus,
} from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/contexts/auth-context";

const nav = [
  { label: "Overview", href: "/app", icon: Activity },
  { label: "Domains", href: "/app/domains", icon: Globe2 },
  { label: "Mailboxes", href: "/app/mailboxes", icon: Users },
  { label: "Aliases", href: "/app/aliases", icon: Layers },
  { label: "Forwarding", href: "/app/forwarding", icon: Send },
  // No "DNS Health" entry: /app/dns-health has never existed, so the link 404'd.
  // Per-domain DNS status lives on /app/domains/[id]. Restore a dedicated
  // cross-domain page here only once that route is actually built.
  { label: "Spam", href: "/app/spam", icon: Shield },
  { label: "Queue", href: "/app/queue", icon: Clock },
  { label: "Logs", href: "/app/logs", icon: FileText },
  { label: "Backups", href: "/app/backups", icon: DatabaseBackup },
  { label: "Billing", href: "/app/billing", icon: CreditCard },
  { label: "Team", href: "/app/team", icon: UserPlus },
  { label: "Settings", href: "/app/settings", icon: Settings },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, user, tenant, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push("/login");
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="h-8 w-8 animate-spin border-2 border-slate-950 border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated) return null;

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-950">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-slate-200 bg-white md:flex">
        {/* Logo */}
        <div className="flex h-14 items-center gap-3 border-b border-slate-200 px-5">
          <BrandMark size={32} preload />
          <span className="font-black">MateMail</span>
        </div>

        {/* Workspace badge */}
        {tenant && (
          <div className="border-b border-slate-100 px-5 py-3">
            <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Workspace</p>
            <p className="mt-0.5 truncate text-sm font-semibold text-slate-800">
              {tenant.name}
            </p>
          </div>
        )}

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-3">
          {nav.map(({ label, href, icon: Icon }) => {
            const active = pathname === href || (href !== "/app" && pathname.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 px-3 py-2 text-sm font-semibold transition ${
                  active
                    ? "bg-cyan-50 text-cyan-700"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-950"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* User footer */}
        <div className="border-t border-slate-200 px-5 py-4">
          <p className="truncate text-xs font-semibold text-slate-700">
            {user?.full_name || user?.email}
          </p>
          <p className="truncate text-xs text-slate-400">{user?.email}</p>
          {user?.is_platform_admin && (
            <Link
              href="/admin"
              className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-red-500 hover:text-red-700"
            >
              <ExternalLink className="h-3 w-3" />
              Admin console
            </Link>
          )}
          <button
            onClick={handleLogout}
            className="mt-2 flex items-center gap-2 text-xs font-semibold text-slate-500 hover:text-rose-600"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex flex-1 flex-col md:pl-64">
        {children}
      </main>
    </div>
  );
}
