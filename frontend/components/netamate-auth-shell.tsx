"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";

import { NetaMateBrand } from "@/components/netamate-brand";

type Theme = "light" | "dark";

const STORAGE_KEY = "netamate.email.login.theme";
const themeListeners = new Set<() => void>();

function readTheme(): Theme {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

const themeStore = {
  subscribe(onChange: () => void) {
    window.addEventListener("storage", onChange);
    themeListeners.add(onChange);
    return () => {
      window.removeEventListener("storage", onChange);
      themeListeners.delete(onChange);
    };
  },
  getSnapshot: readTheme,
  getServerSnapshot: (): Theme => "light",
};

export function NetaMateAuthShell({
  surface,
  eyebrow,
  title,
  description,
  children,
}: {
  surface: "PostBox" | "MailAdmin";
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  const theme = useSyncExternalStore(
    themeStore.subscribe,
    themeStore.getSnapshot,
    themeStore.getServerSnapshot,
  );

  function choose(next: Theme) {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The selected theme still applies to this tab when storage is blocked.
    }
    themeListeners.forEach((listener) => listener());
  }

  return (
    <div className="nm-auth" data-theme={theme}>
      <main className="flex min-h-screen items-center justify-center px-4 py-10">
        <section className="nm-auth-card w-full max-w-[430px]" aria-label={surface + " sign in"}>
          <div className="nm-auth-accent" aria-hidden="true" />

          <header className="flex items-center justify-between gap-4 px-7 pb-5 pt-7">
            <NetaMateBrand surface={surface} preload />
            <div className="nm-theme-toggle flex border" role="radiogroup" aria-label="Appearance">
              <button
                type="button"
                role="radio"
                aria-checked={theme === "light"}
                aria-label="Light mode"
                title="Light mode"
                className="nm-theme-button"
                data-active={theme === "light" ? "true" : "false"}
                onClick={() => choose("light")}
              >
                <Sun className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={theme === "dark"}
                aria-label="Dark mode"
                title="Dark mode"
                className="nm-theme-button"
                data-active={theme === "dark" ? "true" : "false"}
                onClick={() => choose("dark")}
              >
                <Moon className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </header>

          <div className="nm-auth-divider" />

          <div className="px-7 pb-7 pt-6">
            <p className="nm-auth-eyebrow">{eyebrow}</p>
            <h1 className="nm-auth-title mt-2">{title}</h1>
            <p className="nm-auth-description mt-2">{description}</p>
            <div className="mt-6">{children}</div>
          </div>
        </section>
      </main>
    </div>
  );
}
