"use client";

import { useEffect } from "react";
import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/contexts/auth-context";

/**
 * Backward-compatible landing for old /workspaces links.
 *
 * MateMail Hub is single-organization: a tenant user has one organization and
 * there is nothing to choose or create here.
 */
export default function WorkspacesPage() {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuth();

  useEffect(() => {
    if (isLoading) return;
    router.replace(isAuthenticated ? "/app" : "/login");
  }, [isAuthenticated, isLoading, router]);

  return (
    <div className="auth-premium grid min-h-screen place-items-center">
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Opening your organization…
      </div>
    </div>
  );
}
