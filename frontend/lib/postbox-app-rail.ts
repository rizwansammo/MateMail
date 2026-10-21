"use client";

import { useCallback, useSyncExternalStore } from "react";

export type AppRailArea = "mailbox" | "settings";
const KEY = "matemail.postbox.appRail.";
const CHANGE = "matemail:app-rail-preference";

function visibleFor(area: AppRailArea): boolean {
  try { return window.localStorage.getItem(KEY + area) !== "off"; }
  catch { return true; }
}
function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}
/** Both rails default On. Only browser UI choices are stored, never mail data. */
export function usePostBoxAppRail(area: AppRailArea) {
  const visible = useSyncExternalStore(subscribe, () => visibleFor(area), () => true);
  const setVisible = useCallback((next: boolean) => {
    try {
      window.localStorage.setItem(KEY + area, next ? "on" : "off");
      window.dispatchEvent(new Event(CHANGE));
    } catch {
      // Disabled browser storage must not prevent access to Mailbox.
    }
  }, [area]);
  return { visible, setVisible };
}


/** Collapsing is shared between Mailbox and Settings but never changes
 * their independent Show App Sidebar preferences. */
const COLLAPSE_KEY = "matemail.postbox.appRail.collapsed";

function collapsedFromStorage(): boolean {
  try { return window.localStorage.getItem(COLLAPSE_KEY) === "on"; }
  catch { return false; }
}

export function usePostBoxAppRailCollapse() {
  const collapsed = useSyncExternalStore(subscribe, collapsedFromStorage, () => false);
  const setCollapsed = useCallback((next: boolean) => {
    try {
      window.localStorage.setItem(COLLAPSE_KEY, next ? "on" : "off");
      window.dispatchEvent(new Event(CHANGE));
    } catch {
      // Private browsing storage restrictions must never block navigation.
    }
  }, []);
  return { collapsed, setCollapsed };
}
