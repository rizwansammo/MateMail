"use client";

/**
 * The PostBox session, preferences and theme.
 *
 * One context rather than three, because they are read together on every
 * screen and a preference change (theme, density) has to reach the shell, the
 * list and the reader at the same moment.
 *
 * THE THEME IS SERVER-SIDE STATE
 *   Somebody who sets dark mode on their laptop expects dark mode on their
 *   phone, so the choice is a mailbox preference rather than a browser one.
 *   It is applied optimistically and then persisted — a theme toggle that
 *   waits for a round trip feels broken.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  postbox,
  PostBoxError,
  type MailboxProfile,
  type Preferences,
} from "@/lib/postbox-api";

const DEFAULT_PREFERENCES: Preferences = {
  theme: "system",
  density: "comfortable",
  reading_pane: "right",
  list_view: "conversations",
  reader_view: "thread",
  load_remote_images: false,
  messages_per_page: 50,
  timezone_name: "UTC",
  notify_in_app: true,
  notify_sound: false,
  default_identity: "",
};

interface PostBoxValue {
  mailbox: MailboxProfile | null;
  preferences: Preferences;
  isLoading: boolean;
  signIn: (email: string, password: string, remember: boolean) => Promise<void>;
  signOut: () => Promise<void>;
  updatePreferences: (patch: Partial<Preferences>) => Promise<void>;
  refresh: () => void;
}

const PostBoxContext = createContext<PostBoxValue | null>(null);

function applyTheme(theme: Preferences["theme"]) {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-pb-theme");
  } else {
    root.setAttribute("data-pb-theme", theme);
  }
}

export function PostBoxProvider({ children }: { children: React.ReactNode }) {
  const [mailbox, setMailbox] = useState<MailboxProfile | null>(null);
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [isLoading, setIsLoading] = useState(true);

  // `nonce` exists so `refresh()` can re-run this without the effect writing
  // state on its way in, which is what react-hooks/set-state-in-effect flags.
  const [nonce, setNonce] = useState(0);
  const load = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;

    postbox
      .me()
      .then((data) => {
        if (cancelled) return;
        setMailbox(data.mailbox);
        setPreferences({ ...DEFAULT_PREFERENCES, ...data.preferences });
        applyTheme(data.preferences?.theme ?? "system");
      })
      .catch(() => {
        // Not signed in, or the session ended. Either way there is no mailbox.
        if (!cancelled) setMailbox(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const signIn = useCallback(
    async (email: string, password: string, remember: boolean) => {
      const data = await postbox.login(email, password, remember);
      setMailbox(data.mailbox);
      // Preferences are fetched rather than assumed: a returning person's
      // theme should be right on the first paint after signing in.
      const me = await postbox.me();
      setPreferences({ ...DEFAULT_PREFERENCES, ...me.preferences });
      applyTheme(me.preferences?.theme ?? "system");
    },
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await postbox.logout();
    } catch {
      // The cookie is cleared server-side on success; if the call itself
      // failed there is nothing useful to say, and the state below is what
      // stops PostBox rendering either way.
    }
    setMailbox(null);
  }, []);

  const updatePreferences = useCallback(
    async (patch: Partial<Preferences>) => {
      // Applied first. A theme toggle that waits for the network feels broken,
      // and the server is the authority only for what persists.
      const next = { ...preferences, ...patch };
      setPreferences(next);
      if (patch.theme) applyTheme(patch.theme);

      try {
        const saved = await postbox.savePreferences(patch);
        setPreferences({ ...DEFAULT_PREFERENCES, ...saved });
        applyTheme(saved.theme);
      } catch (error) {
        // Roll back, so the UI never shows a preference that was refused.
        setPreferences(preferences);
        applyTheme(preferences.theme);
        throw error;
      }
    },
    [preferences],
  );

  const value = useMemo(
    () => ({
      mailbox,
      preferences,
      isLoading,
      signIn,
      signOut,
      updatePreferences,
      refresh: load,
    }),
    [mailbox, preferences, isLoading, signIn, signOut, updatePreferences, load],
  );

  return <PostBoxContext.Provider value={value}>{children}</PostBoxContext.Provider>;
}

export function usePostBox(): PostBoxValue {
  const context = useContext(PostBoxContext);
  if (!context) {
    throw new Error("usePostBox must be used inside PostBoxProvider");
  }
  return context;
}

/** The message an API failure should show, without leaking internals. */
export function describePostBoxError(error: unknown, fallback: string): string {
  if (error instanceof PostBoxError) return error.message;
  return fallback;
}
