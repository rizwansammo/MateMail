"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  Building2,
  LogOut,
  ShieldCheck,
} from "lucide-react";
import { useAuth } from "@/contexts/auth-context";

const nav = [
  { label: "Dashboard", href: "/admin", icon: Activity },
  { label: "Tenants", href: "/admin/tenants", icon: Building2 },
];

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading, user, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push("/login");
    }
    if (!isLoading && isAuthenticated && !user?.is_platform_admin) {
      router.push("/app");
    }
  }, [isLoading, isAuthenticated, user, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="h-8 w-8 animate-spin border-2 border-slate-950 border-t-transparent" />
      </div>
    );
  }

  if (!isAuthenticated || !user?.is_platform_admin) return null;

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  return (
    <div className="flex min-h-screen bg-slate-950 text-slate-100">
      {/* Sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-56 flex-col border-r border-slate-800 bg-slate-900 md:flex">
        {/* Logo */}
        <div className="flex h-14 items-center gap-3 border-b border-slate-800 px-5">
          <div className="grid h-8 w-8 place-items-center bg-red-600 text-white">
            <ShieldCheck className="h-4 w-4" />
          </div>
          <div>
            <span className="font-black text-white">MateMail</span>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-red-400">
              Platform Admin
            </p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-3">
          {nav.map(({ label, href, icon: Icon }) => {
            const active = pathname === href || (href !== "/admin" && pathname.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm font-semibold transition ${
                  active
                    ? "bg-slate-700 text-white"
                    : "text-slate-400 hover:bg-slate-800 hover:text-white"
                }`}
              >
                <Icon className="h-4 w-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>

        {/* Footer */}
        <div className="border-t border-slate-800 px-5 py-4">
          <p className="truncate text-xs font-semibold text-slate-300">
            {user.full_name || user.email}
          </p>
          <p className="truncate text-xs text-slate-500">{user.email}</p>
          <button
            onClick={handleLogout}
            className="mt-3 flex items-center gap-2 text-xs font-semibold text-slate-500 hover:text-red-400"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex flex-1 flex-col md:pl-56">
        {/* Top bar */}
        <div className="sticky top-0 z-20 flex h-12 items-center border-b border-slate-800 bg-slate-900 px-6">
          <span className="text-xs font-semibold uppercase tracking-widest text-red-400">
            Internal Admin Console
          </span>
          <Link
            href="/app"
            className="ml-auto text-xs text-slate-500 hover:text-slate-300"
          >
            Back to workspace →
          </Link>
        </div>
        <div className="flex-1 bg-slate-950">
          {children}
        </div>
      </main>
    </div>
  );
}
