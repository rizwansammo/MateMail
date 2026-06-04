"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "@/contexts/auth-context";

export default function RootPage() {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading) {
      router.replace(isAuthenticated ? "/app" : "/login");
    }
  }, [isLoading, isAuthenticated, router]);

  return (
    <main className="flex min-h-screen flex-col items-center justify-center bg-slate-50">
      <div className="grid h-12 w-12 place-items-center bg-slate-950 text-white">
        <ShieldCheck className="h-7 w-7" />
      </div>
      <p className="mt-4 text-sm text-slate-400">Loading MateMail…</p>
    </main>
  );
}
