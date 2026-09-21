"use client";

/**
 * Light / dark appearance for the customer Workspace (Portal).
 *
 * This is deliberately independent from PostBox and Platform Console. Each
 * surface owns its own stored preference and DOM attribute so changing Portal
 * appearance can never restyle PostBox.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
} from "react";
import { Moon, Sun } from "lucide-react";

export type WorkspaceThemeChoice = "light" | "dark";

const STORAGE_KEY = "matemail.workspace.theme";

interface WorkspaceThemeValue {
  theme: WorkspaceThemeChoice;
  setTheme: (next: WorkspaceThemeChoice) => void;
}

const WorkspaceThemeContext = createContext<WorkspaceThemeValue | null>(null);
const listeners = new Set<() => void>();

function readStored(): WorkspaceThemeChoice {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function apply(theme: WorkspaceThemeChoice) {
  document.documentElement.setAttribute("data-ws-theme", theme);
}

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
  getServerSnapshot: (): WorkspaceThemeChoice => "light",
};

export function WorkspaceThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useSyncExternalStore(
    themeStore.subscribe,
    themeStore.getSnapshot,
    themeStore.getServerSnapshot,
  );

  useEffect(() => {
    apply(theme);
  }, [theme]);

  const setTheme = useCallback((next: WorkspaceThemeChoice) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Applying the choice for this tab is still useful when storage is blocked.
    }
    apply(next);
    listeners.forEach((listener) => listener());
  }, []);

  const value = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);

  return (
    <WorkspaceThemeContext.Provider value={value}>
      {children}
    </WorkspaceThemeContext.Provider>
  );
}

export function useWorkspaceTheme(): WorkspaceThemeValue {
  const context = useContext(WorkspaceThemeContext);
  if (!context) {
    throw new Error("useWorkspaceTheme must be used inside WorkspaceThemeProvider");
  }
  return context;
}

export function WorkspaceThemeToggle() {
  const { theme, setTheme } = useWorkspaceTheme();

  return (
    <div
      className="ws-theme-toggle grid grid-cols-2 border border-slate-200"
      role="radiogroup"
      aria-label="Workspace appearance"
    >
      <ThemeButton
        label="Light"
        active={theme === "light"}
        onClick={() => setTheme("light")}
        Icon={Sun}
      />
      <ThemeButton
        label="Dark"
        active={theme === "dark"}
        onClick={() => setTheme("dark")}
        Icon={Moon}
      />
    </div>
  );
}

function ThemeButton({
  label,
  active,
  onClick,
  Icon,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  Icon: typeof Sun;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className="flex items-center justify-center gap-1.5 px-2 py-1.5 text-[11px] font-semibold transition"
      style={{
        background: active ? "var(--ws-primary)" : "var(--ws-surface)",
        color: active ? "var(--ws-primary-fg)" : "var(--ws-text-muted)",
      }}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {label}
    </button>
  );
}
