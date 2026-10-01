"use client";

import { useEffect, useState } from "react";
import { Building2, ChevronRight, Loader2, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  AuthBrand,
  AuthButton,
  AuthError,
  AuthField,
  titleCase,
  workspaceInitials,
} from "@/components/workspace/premium-auth";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError } from "@/lib/api";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

interface WorkspaceListItem {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  role: string;
}

interface CreateWorkspaceResult {
  access: string;
  tenant: {
    id: string;
    name: string;
    slug: string;
    status: string;
    plan?: string;
  };
}

function apiDetail(caught: unknown, fallback: string) {
  if (!(caught instanceof ApiError)) return fallback;
  try {
    const body = JSON.parse(caught.message);
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export default function WorkspacesPage() {
  const router = useRouter();
  const {
    user,
    tenant,
    isLoading: authLoading,
    isAuthenticated,
    switchWorkspace,
    logout,
  } = useAuth();
  const [workspaces, setWorkspaces] = useState<WorkspaceListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [switchingId, setSwitchingId] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [workspaceName, setWorkspaceName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (authLoading) return;
    if (!isAuthenticated) {
      router.replace("/login");
      return;
    }

    let cancelled = false;
    api
      .get<WorkspaceListItem[]>("/api/workspaces/")
      .then((items) => {
        if (!cancelled) {
          setWorkspaces(items);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError("Could not load your workspaces. Please try again.");
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [authLoading, isAuthenticated, router]);

  async function chooseWorkspace(id: string) {
    setError("");
    setSwitchingId(id);
    try {
      if (tenant?.id !== id) {
        await switchWorkspace(id);
      }
      router.push("/app");
    } catch (caught) {
      setError(apiDetail(caught, "Could not switch workspace."));
      setSwitchingId("");
    }
  }

  async function createWorkspace(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    const name = workspaceName.trim();
    if (!name) return;

    setCreating(true);
    try {
      const result = await api.post<CreateWorkspaceResult>("/api/workspaces/create/", { name });
      await switchWorkspace(result.tenant.id);
      router.push("/app/onboarding");
    } catch (caught) {
      setError(apiDetail(caught, "Could not create the workspace."));
      setCreating(false);
    }
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
  }

  const firstName = user?.full_name?.trim().split(/\s+/)[0] || "there";

  return (
    <div className="auth-premium workspace-entry">
      <header className="workspace-entry-header">
        <AuthBrand />
        <button className="auth-text-button" type="button" onClick={handleLogout}>
          Sign out
        </button>
      </header>

      <main className="workspace-entry-main">
        <div className="auth-eyebrow">
          {IS_NETAMATE_EMAIL ? "YOUR ORGANIZATION" : "YOUR WORKSPACES"}
        </div>
        <h1>Welcome back, {firstName}.</h1>
        <p>
          {IS_NETAMATE_EMAIL
            ? "Choose the authorized organization you’d like to administer."
            : "Choose where you’d like to work today."}
        </p>

        {error && <div className="mt-5"><AuthError>{error}</AuthError></div>}

        {loading || authLoading ? (
          <div className="workspace-loading">
            <Loader2 className="auth-loading-spin h-4 w-4" />
            Loading your workspaces…
          </div>
        ) : (
          <div className="workspace-cards">
            {workspaces.map((workspace) => (
              <button
                type="button"
                className="workspace-entry-card"
                key={workspace.id}
                disabled={!!switchingId}
                onClick={() => chooseWorkspace(workspace.id)}
              >
                <span className="workspace-entry-monogram">
                  {workspaceInitials(workspace.name)}
                </span>
                <span className="workspace-entry-copy">
                  <strong>{workspace.name}</strong>
                  <small>
                    {titleCase(workspace.plan || "Private Beta")} · {titleCase(workspace.role)} · {titleCase(workspace.status)}
                  </small>
                </span>
                {switchingId === workspace.id ? (
                  <Loader2 className="auth-loading-spin h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </button>
            ))}

            {!IS_NETAMATE_EMAIL && !showCreate && (
              <button
                type="button"
                className="workspace-create-card"
                onClick={() => {
                  setShowCreate(true);
                  setError("");
                }}
              >
                <Plus className="h-5 w-5" />
                <span>Create a new workspace</span>
              </button>
            )}
          </div>
        )}

        {!IS_NETAMATE_EMAIL && showCreate && (
          <form className="workspace-create-panel" onSubmit={createWorkspace}>
            <div className="flex items-start gap-3">
              <span className="auth-form-icon !mb-0 !h-10 !w-10 !rounded-[10px]">
                <Building2 className="h-5 w-5" />
              </span>
              <div>
                <h2>Create a new workspace</h2>
                <p>
                  MateMail will generate the permanent unique workspace identifier automatically.
                  New workspaces require platform approval before mail provisioning is enabled.
                </p>
              </div>
            </div>
            <div className="mt-5">
              <AuthField label="Organization name">
                <input
                  className="auth-input"
                  type="text"
                  autoFocus
                  required
                  maxLength={255}
                  value={workspaceName}
                  onChange={(event) => setWorkspaceName(event.target.value)}
                  placeholder="Harbor & Co."
                />
              </AuthField>
            </div>
            <div className="workspace-create-actions">
              <AuthButton type="submit" loading={creating}>
                {creating ? "Creating…" : "Create workspace"}
              </AuthButton>
              <AuthButton
                type="button"
                secondary
                disabled={creating}
                onClick={() => {
                  setShowCreate(false);
                  setWorkspaceName("");
                  setError("");
                }}
              >
                Cancel
              </AuthButton>
            </div>
          </form>
        )}
      </main>
    </div>
  );
}
