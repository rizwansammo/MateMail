"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { api, ApiError } from "@/lib/api";
import { clearTokens, getRefreshToken, setTokens } from "@/lib/auth";

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
    workspace_name: string
  ) => Promise<void>;
  logout: () => Promise<void>;
  verify2fa: (partial_token: string, code: string) => Promise<void>;
  switchWorkspace: (tenant_id: string) => Promise<void>;
  setAuthResult: (data: { access: string; refresh: string; user: AuthUser; tenant: AuthTenant }) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [tenant, setTenant] = useState<AuthTenant | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const setAuthResult = useCallback(
    (data: { access: string; refresh: string; user: AuthUser; tenant: AuthTenant }) => {
      setTokens(data.access, data.refresh);
      setUser(data.user);
      setTenant(data.tenant);
    },
    []
  );

  // Restore session from stored refresh token on mount
  useEffect(() => {
    const restore = async () => {
      const refresh = getRefreshToken();
      if (!refresh) {
        setIsLoading(false);
        return;
      }
      try {
        const data = await api.post<{
          access: string;
          refresh: string;
        }>("/api/auth/refresh/", { refresh });
        setTokens(data.access, data.refresh ?? refresh);
        const me = await api.get<AuthUser>("/api/auth/me/");
        setUser(me);
        // Restore tenant from stored token payload
        const { getTokenPayload } = await import("@/lib/auth");
        const payload = getTokenPayload(data.access);
        if (payload?.tenant_id) {
          try {
            const ws = await api.get<AuthTenant>(`/api/workspaces/${payload.tenant_id}/`);
            setTenant(ws);
          } catch {
            // tenant lookup failed — leave as null
          }
        }
      } catch {
        clearTokens();
      } finally {
        setIsLoading(false);
      }
    };
    restore();
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const data = await api.post<{
        requires_2fa?: boolean;
        partial_token?: string;
        access?: string;
        refresh?: string;
        user?: AuthUser;
        tenant?: AuthTenant;
      }>("/api/auth/login/", { email, password });

      if (data.requires_2fa) {
        return { requires_2fa: true, partial_token: data.partial_token };
      }

      setTokens(data.access!, data.refresh!);
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
      workspace_name: string
    ) => {
      const data = await api.post<{
        access: string;
        refresh: string;
        user: AuthUser;
        tenant: AuthTenant;
      }>("/api/auth/signup/", { email, password, full_name, workspace_name });

      setTokens(data.access, data.refresh);
      setUser(data.user);
      setTenant(data.tenant);
    },
    []
  );

  const logout = useCallback(async () => {
    const refresh = getRefreshToken();
    try {
      if (refresh) await api.post("/api/auth/logout/", { refresh });
    } catch {
      // best-effort
    }
    clearTokens();
    setUser(null);
    setTenant(null);
  }, []);

  const verify2fa = useCallback(
    async (partial_token: string, code: string) => {
      const data = await api.post<{
        access: string;
        refresh: string;
        user: AuthUser;
        tenant: AuthTenant;
      }>("/api/auth/2fa/verify/", { partial_token, code });
      setTokens(data.access, data.refresh);
      setUser(data.user);
      setTenant(data.tenant);
    },
    []
  );

  const switchWorkspace = useCallback(async (tenant_id: string) => {
    const data = await api.post<{
      access: string;
      refresh: string;
      tenant: AuthTenant;
    }>("/api/workspaces/switch/", { tenant_id });
    setTokens(data.access, data.refresh);
    setTenant(data.tenant);
  }, []);

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
        switchWorkspace,
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
