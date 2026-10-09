"use client";

/**
 * The PostBox session, active mailbox and personal display preferences.
 *
 * Authentication always belongs to one personal mailbox. `mailbox` is the
 * mailbox currently open in the UI and may be an authorised TeamBox.
 * `authenticatedMailbox` never changes until account sign-out/switch.
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
  type AvailablePostBoxMailbox,
  type MailboxPermissions,
  type MailboxProfile,
  type Preferences,
} from "@/lib/postbox-api";

const DEFAULT_PREFERENCES: Preferences = {
  theme: "light",
  density: "extra_compact",
  reading_pane: "off",
  list_view: "conversations",
  reader_view: "thread",
  load_remote_images: false,
  messages_per_page: 50,
  timezone_name: "UTC",
  notify_in_app: true,
  notify_sound: false,
  default_identity: "",
};

const OWNER_PERMISSIONS: MailboxPermissions = {
  can_read: true,
  can_manage: true,
  can_send_as: true,
  can_send_on_behalf: true,
};

interface PostBoxValue {
  mailbox: MailboxProfile | null;
  authenticatedMailbox: MailboxProfile | null;
  availableMailboxes: AvailablePostBoxMailbox[];
  permissions: MailboxPermissions;
  preferences: Preferences;
  isLoading: boolean;
  signIn: (email: string, password: string, remember: boolean) => Promise<void>;
  signOut: () => Promise<void>;
  switchMailbox: (mailboxId: string | null) => Promise<void>;
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
  const [authenticatedMailbox, setAuthenticatedMailbox] =
    useState<MailboxProfile | null>(null);
  const [availableMailboxes, setAvailableMailboxes] =
    useState<AvailablePostBoxMailbox[]>([]);
  const [permissions, setPermissions] =
    useState<MailboxPermissions>(OWNER_PERMISSIONS);
  const [preferences, setPreferences] = useState<Preferences>(DEFAULT_PREFERENCES);
  const [isLoading, setIsLoading] = useState(true);

  const [nonce, setNonce] = useState(0);
  const load = useCallback(() => setNonce((value) => value + 1), []);

  const applyMe = useCallback((data: Awaited<ReturnType<typeof postbox.me>>) => {
    setMailbox(data.mailbox);
    setAuthenticatedMailbox(data.authenticated_mailbox);
    setAvailableMailboxes(data.available_mailboxes);
    setPermissions(data.permissions);
    setPreferences({ ...DEFAULT_PREFERENCES, ...data.preferences });
    applyTheme(data.preferences?.theme ?? DEFAULT_PREFERENCES.theme);
  }, []);

  useEffect(() => {
    let cancelled = false;

    postbox
      .me()
      .then((data) => {
        if (!cancelled) applyMe(data);
      })
      .catch(() => {
        if (!cancelled) {
          setMailbox(null);
          setAuthenticatedMailbox(null);
          setAvailableMailboxes([]);
          setPermissions(OWNER_PERMISSIONS);
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [applyMe, nonce]);

  const signIn = useCallback(
    async (email: string, password: string, remember: boolean) => {
      await postbox.login(email, password, remember);
      applyMe(await postbox.me());
    },
    [applyMe],
  );

  const signOut = useCallback(async () => {
    try {
      await postbox.logout();
    } catch {
      // Local state still clears so an ended/failed session never keeps mail UI
      // visible as if it were authenticated.
    }
    setMailbox(null);
    setAuthenticatedMailbox(null);
    setAvailableMailboxes([]);
    setPermissions(OWNER_PERMISSIONS);
  }, []);

  const switchMailbox = useCallback(async (mailboxId: string | null) => {
    const data = await postbox.switchMailbox(mailboxId);
    setMailbox(data.mailbox);
    setAuthenticatedMailbox(data.authenticated_mailbox);
    setAvailableMailboxes(data.available_mailboxes);
    setPermissions(data.permissions);
    setPreferences({ ...DEFAULT_PREFERENCES, ...data.preferences });
    applyTheme(data.preferences?.theme ?? DEFAULT_PREFERENCES.theme);
  }, []);

  const updatePreferences = useCallback(
    async (patch: Partial<Preferences>) => {
      const next = { ...preferences, ...patch };
      setPreferences(next);
      if (patch.theme) applyTheme(patch.theme);

      try {
        const saved = await postbox.savePreferences(patch);
        setPreferences({ ...DEFAULT_PREFERENCES, ...saved });
        applyTheme(saved.theme);
      } catch (error) {
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
      authenticatedMailbox,
      availableMailboxes,
      permissions,
      preferences,
      isLoading,
      signIn,
      signOut,
      switchMailbox,
      updatePreferences,
      refresh: load,
    }),
    [
      mailbox,
      authenticatedMailbox,
      availableMailboxes,
      permissions,
      preferences,
      isLoading,
      signIn,
      signOut,
      switchMailbox,
      updatePreferences,
      load,
    ],
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

export function describePostBoxError(error: unknown, fallback: string): string {
  if (error instanceof PostBoxError) return error.message;
  return fallback;
}
