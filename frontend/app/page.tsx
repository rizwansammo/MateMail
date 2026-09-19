"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { BrandMark } from "@/components/brand-mark";
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
      <BrandMark size={48} preload />
      <p className="mt-4 text-sm text-slate-400">Loading MateMail…</p>
    </main>
  );
}
