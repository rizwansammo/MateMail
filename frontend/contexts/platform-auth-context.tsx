"use client";

/**
 * The Platform Console session.
 *
 * Separate from `AuthProvider` because the two logins are different
 * protocols, not different skins. The organization login may finish in one
 * step; this one never does — `signIn` returns a challenge and nothing else,
 * and only `verifyCode` can produce a session.
 *
 * The access token is held the same way the organization console holds it, in
 * `lib/auth`, so the shared `lib/api` refresh path works unchanged. What is
 * different is the guard: a session belonging to somebody who is not a
 * platform administrator is treated as no session at all.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { api, ApiError } from "@/lib/api";
import { clearTokens, setAccessToken } from "@/lib/auth";

export interface PlatformUser {
  id: string;
  email: string;
  full_name: string;
  is_platform_admin: boolean;
}

export interface ChallengeState {
  challenge: string;
  expiresIn: number;
  sentTo: string;
}

interface PlatformAuthValue {
  user: PlatformUser | null;
  isLoading: boolean;
  /** Stage one. Resolves with the challenge; never with a session. */
  signIn: (email: string, password: string) => Promise<ChallengeState>;
  /** Stage two. The only call that produces a session. */
  verifyCode: (challenge: string, code: string) => Promise<PlatformUser>;
  resendCode: (challenge: string) => Promise<ChallengeState>;
  signOut: () => Promise<void>;
}

const PlatformAuthContext = createContext<PlatformAuthValue | null>(null);

interface ChallengeResponse {
  challenge: string;
  expires_in: number;
  sent_to?: string;
}

function toChallenge(data: ChallengeResponse): ChallengeState {
  return {
    challenge: data.challenge,
    expiresIn: data.expires_in,
    sentTo: data.sent_to ?? "",
  };
}

export function PlatformAuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<PlatformUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Restore on mount by exchanging the refresh cookie, exactly as the
  // organization console does. A restored session still has to prove it
  // belongs to a platform administrator — a customer who somehow arrived here
  // with a valid tenant session must not see a console shell before the API
  // starts returning 403s.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const refreshed = await api.post<{ access: string }>("/api/auth/refresh/");
        if (cancelled) return;
        setAccessToken(refreshed.access);

        const me = await api.get<PlatformUser>("/api/auth/me/");
        if (cancelled) return;
        setUser(me.is_platform_admin ? me : null);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const data = await api.post<ChallengeResponse>("/api/platform/auth/login/", {
      email,
      password,
    });
    return toChallenge(data);
  }, []);

  const verifyCode = useCallback(async (challenge: string, code: string) => {
    const data = await api.post<{ access: string; user: PlatformUser }>(
      "/api/platform/auth/verify/",
      { challenge, code },
    );
    setAccessToken(data.access);
    setUser(data.user);
    return data.user;
  }, []);

  const resendCode = useCallback(async (challenge: string) => {
    const data = await api.post<ChallengeResponse>("/api/platform/auth/resend/", {
      challenge,
    });
    return toChallenge(data);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.post("/api/auth/logout/");
    } catch {
      // The cookie is cleared server-side on a successful call; if the call
      // itself failed there is nothing useful to tell the operator, and the
      // local state below is what stops the console rendering either way.
    }
    clearTokens();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, isLoading, signIn, verifyCode, resendCode, signOut }),
    [user, isLoading, signIn, verifyCode, resendCode, signOut],
  );

  return (
    <PlatformAuthContext.Provider value={value}>
      {children}
    </PlatformAuthContext.Provider>
  );
}

export function usePlatformAuth(): PlatformAuthValue {
  const context = useContext(PlatformAuthContext);
  if (!context) {
    throw new Error("usePlatformAuth must be used inside PlatformAuthProvider");
  }
  return context;
}

/** The message an API failure should show, without leaking a stack trace. */
export function describeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    try {
      const parsed = JSON.parse(error.message) as Record<string, unknown>;
      if (typeof parsed.detail === "string") return parsed.detail;
      const first = Object.values(parsed)[0];
      if (Array.isArray(first) && typeof first[0] === "string") return first[0];
    } catch {
      // Not JSON — fall through to the generic message rather than showing
      // the raw body, which may be an HTML error page.
    }
  }
  return fallback;
}
