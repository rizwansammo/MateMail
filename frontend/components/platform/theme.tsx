"use client";

/**
 * Light / dark for the Platform Console.
 *
 * Three states, not two: "light", "dark", and the default "system", which
 * stamps nothing on <html> and lets `prefers-color-scheme` decide. Most people
 * never touch a toggle, so the un-stamped document is the common case — the
 * CSS in globals.css is written so that case is correct on its own.
 *
 * The preference is per-browser and lives in localStorage. Every read and
 * write is wrapped: a private window, blocked site data or a thumbnail capture
 * can make the accessor throw, and a theme toggle is not worth a blank page.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";
import { Monitor, Moon, Sun } from "lucide-react";

export type ThemeChoice = "light" | "dark" | "system";

const STORAGE_KEY = "matemail.platform.theme";

interface ThemeValue {
  theme: ThemeChoice;
  setTheme: (next: ThemeChoice) => void;
}

const ThemeContext = createContext<ThemeValue | null>(null);

function readStored(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (value === "light" || value === "dark" || value === "system") return value;
  } catch {
    // Unavailable storage is not an error worth surfacing; "system" is a
    // perfectly good answer.
  }
  return "system";
}

function apply(theme: ThemeChoice) {
  const root = document.documentElement;
  if (theme === "system") {
    root.removeAttribute("data-pf-theme");
  } else {
    root.setAttribute("data-pf-theme", theme);
  }
}

/**
 * The stored choice, as an external store.
 *
 * `useSyncExternalStore` rather than "start at system, then correct it in an
 * effect": the effect version is a synchronous setState in an effect body, and
 * it renders one frame of the wrong theme before correcting itself. This reads
 * the real value during render on the client, and returns "system" for the
 * server snapshot so the markup matches.
 */
const themeStore = {
  subscribe(onChange: () => void) {
    window.addEventListener("storage", onChange);
    listeners.add(onChange);
    return () => {
      window.removeEventListener("storage", onChange);
      listeners.delete(onChange);
    };
  },
  getSnapshot: readStored,
  getServerSnapshot: (): ThemeChoice => "system",
};

const listeners = new Set<() => void>();

export function PlatformThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useSyncExternalStore(
    themeStore.subscribe,
    themeStore.getSnapshot,
    themeStore.getServerSnapshot,
  );

  // Keeping the DOM attribute in step with the store is a side effect on an
  // external system, which is what effects are actually for.
  useEffect(() => {
    apply(theme);
  }, [theme]);

  const setTheme = useCallback((next: ThemeChoice) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The choice still applies for this page; it just will not be
      // remembered. Failing the click would be worse.
    }
    apply(next);
    // `storage` does not fire in the tab that wrote the value, so the store's
    // own subscribers are notified explicitly.
    listeners.forEach((listener) => listener());
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function usePlatformTheme(): ThemeValue {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("usePlatformTheme must be used inside PlatformThemeProvider");
  }
  return context;
}

const OPTIONS: Array<{ value: ThemeChoice; label: string; Icon: typeof Sun }> = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "system", label: "System", Icon: Monitor },
  { value: "dark", label: "Dark", Icon: Moon },
];

/**
 * A three-way segmented control rather than a two-way switch.
 *
 * A switch cannot express "follow the system", so it forces a choice the
 * operator did not want to make and then keeps it forever.
 */
export function ThemeToggle() {
  const { theme, setTheme } = usePlatformTheme();

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      className="inline-flex items-center gap-0.5 rounded-lg p-0.5"
      style={{ background: "var(--pf-surface-3)" }}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={label}
            title={label}
            onClick={() => setTheme(value)}
            className="pf-btn"
            style={{
              padding: "0.25rem 0.45rem",
              background: active ? "var(--pf-surface)" : "transparent",
              color: active ? "var(--pf-text)" : "var(--pf-text-muted)",
              boxShadow: active ? "var(--pf-shadow)" : "none",
            }}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}

/**
 * Applies the stored theme before the first paint.
 *
 * Without this the page renders light, the effect above runs, and a dark-mode
 * operator sees a white flash on every navigation. It is inline because it has
 * to run before the body does, and it carries the CSP nonce for the same
 * reason every other script tag does.
 */
export function ThemeScript({ nonce }: { nonce?: string }) {
  const source = `(function(){try{var v=localStorage.getItem(${JSON.stringify(
    STORAGE_KEY,
  )});if(v==="light"||v==="dark"){document.documentElement.setAttribute("data-pf-theme",v);}}catch(e){}})();`;

  return <script nonce={nonce} dangerouslySetInnerHTML={{ __html: source }} />;
}
