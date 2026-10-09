"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { api, restoreAccessToken } from "@/lib/api";
import { clearTokens, purgeLegacyRefreshToken, setAccessToken } from "@/lib/auth";

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  email_verified: boolean;
  two_factor_enabled: boolean;
  is_platform_admin: boolean;
}

export interface AuthTenant {
  id: string;
  name: string;
  slug: string;
  status: string;
  role?: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  tenant: AuthTenant | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (
    email: string,
    password: string
  ) => Promise<{ requires_2fa: boolean; partial_token?: string }>;
  signup: (
    email: string,
    password: string,
    full_name: string,
    workspace_name?: string,
    invite_token?: string
  ) => Promise<void>;
  logout: () => Promise<void>;
  verify2fa: (partial_token: string, code: string) => Promise<void>;
  refreshTenant: () => Promise<void>;
  setAuthResult: (data: { access: string; user: AuthUser; tenant: AuthTenant }) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [tenant, setTenant] = useState<AuthTenant | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const setAuthResult = useCallback(
    (data: { access: string; user: AuthUser; tenant: AuthTenant }) => {
      // The refresh token is not here to be stored — it arrived as an
      // HttpOnly cookie the server set on this response.
      setAccessToken(data.access);
      setUser(data.user);
      setTenant(data.tenant);
    },
    []
  );

  // Restore the session on mount by exchanging the refresh cookie.
  //
  // There is nothing to check beforehand: the cookie is invisible to this
  // code, so the only way to know whether a session exists is to ask. A 401
  // simply means "not signed in" and is not an error worth surfacing.
  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      // A browser that used a pre-P3c build still holds a live refresh token
      // in localStorage. Clear it on the first load of the new build.
      purgeLegacyRefreshToken();
      try {
        const access = await restoreAccessToken();
        if (!access || cancelled) return;
        const me = await api.get<AuthUser>("/api/auth/me/");
        if (cancelled) return;
        setUser(me);
        // Restore tenant from stored token payload
        const { getTokenPayload } = await import("@/lib/auth");
        const payload = getTokenPayload(access);
        if (payload?.tenant_id) {
          try {
            const ws = await api.get<AuthTenant>(`/api/workspaces/${payload.tenant_id}/`);
            if (!cancelled) setTenant(ws);
          } catch {
            // tenant lookup failed — leave as null
          }
        }
      } catch {
        if (!cancelled) clearTokens();
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    restore();
    return () => { cancelled = true; };
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const data = await api.post<{
        requires_2fa?: boolean;
        partial_token?: string;
        access?: string;
        user?: AuthUser;
        tenant?: AuthTenant;
      }>("/api/auth/login/", { email, password });

      if (data.requires_2fa) {
        return { requires_2fa: true, partial_token: data.partial_token };
      }

      setAccessToken(data.access!);
      setUser(data.user!);
      setTenant(data.tenant!);
      return { requires_2fa: false };
    },
    []
  );

  const signup = useCallback(
    async (
      email: string,
      password: string,
      full_name: string,
      workspace_name?: string,
      invite_token?: string
    ) => {
      const data = await api.post<{
        access: string;
        user: AuthUser;
        tenant: AuthTenant;
      }>("/api/auth/signup/", {
        email,
        password,
        full_name,
        ...(workspace_name ? { workspace_name } : {}),
        ...(invite_token ? { invite_token } : {}),
      });

      setAccessToken(data.access);
      setUser(data.user);
      setTenant(data.tenant);
    },
    []
  );

  const logout = useCallback(async () => {
    try {
      // No body: the server reads the refresh cookie, blacklists the token and
      // clears the cookie in its response. Always attempted, because this
      // client cannot tell whether a cookie is present.
      await api.post("/api/auth/logout/");
    } catch {
      // best-effort — the local session is cleared either way
    }
    clearTokens();
    setUser(null);
    setTenant(null);
  }, []);

  const verify2fa = useCallback(
    async (partial_token: string, code: string) => {
      const data = await api.post<{
        access: string;
        user: AuthUser;
        tenant: AuthTenant;
      }>("/api/auth/2fa/verify/", { partial_token, code });
      setAccessToken(data.access);
      setUser(data.user);
      setTenant(data.tenant);
    },
    []
  );

  const refreshTenant = useCallback(async () => {
    if (!tenant?.id) return;
    const workspace = await api.get<AuthTenant>(`/api/workspaces/${tenant.id}/`);
    setTenant(workspace);
  }, [tenant?.id]);

  return (
    <AuthContext.Provider
      value={{
        user,
        tenant,
        isLoading,
        isAuthenticated: !!user,
        login,
        signup,
        logout,
        verify2fa,
        refreshTenant,
        setAuthResult,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
