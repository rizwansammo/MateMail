"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrandMark } from "@/components/brand-mark";

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  if (pathname === "/login") return <>{children}</>;

  return (
    <div className="min-h-screen bg-slate-950 p-5 text-slate-950">
      <div className="mb-8">
        <Link
          href="/"
          className="inline-flex items-center gap-3 text-white hover:opacity-80 transition"
        >
          <BrandMark size={36} preload />
          <b className="text-lg">MateMail</b>
        </Link>
      </div>
      <div className="mx-auto max-w-5xl overflow-hidden border border-white/10 bg-white shadow-2xl md:grid md:grid-cols-[0.9fr_1.1fr]">
        <div className="hidden flex-col justify-between bg-slate-950 p-10 md:flex">
          <div>
            <p className="text-sm font-bold uppercase tracking-[0.2em] text-cyan-400">
              MateMail
            </p>
            <h2 className="mt-4 text-3xl font-black leading-tight text-white">
              Professional email hosting for teams that need control.
            </h2>
          </div>
          <div className="space-y-3">
            {[
              "Domain-based inboxes",
              "DNS health monitoring",
              "Team mailbox management",
              "Secure 2FA login",
            ].map((item) => (
              <div key={item} className="flex items-center gap-3 text-sm text-slate-300">
                <div className="h-1.5 w-1.5 bg-cyan-400" />
                {item}
              </div>
            ))}
          </div>
        </div>
        <div className="p-8 md:p-10">{children}</div>
      </div>
    </div>
  );
}
