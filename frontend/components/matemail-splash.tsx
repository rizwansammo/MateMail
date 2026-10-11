"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { needsSettingsSplash, type MateMailSurface } from "@/lib/matemail-splash-routes";
import "./matemail-splash.css";

type SplashStage = "initial" | "short" | "fading" | "hidden";

/**
 * A shared brand splash used by the organization Hub and the PostBox surface.
 * Do not mount in the Platform Console. Folder/query and Settings submenus
 * intentionally never trigger the animation.
 */
export function MateMailSplash({ surface }: { surface: MateMailSurface }) {
  const pathname = usePathname();
  // Initial SSR markup displays the splash before hydration on hard reload.
  const [stage, setStage] = useState<SplashStage>("initial");
  const previousPath = useRef(pathname);

  useEffect(() => {
    if (stage !== "initial" && stage !== "short") return;

    let cancelled = false;
    const minimum = stage === "initial" ? 700 : 300;
    let timer: number | undefined;
    let fontTimeout: number | undefined;
    const elapsed = new Promise<void>((resolve) => {
      timer = window.setTimeout(resolve, minimum);
    });
    const fonts = stage === "initial" && document.fonts
      ? Promise.race([
          document.fonts.ready.then(() => undefined),
          new Promise<void>((resolve) => {
            fontTimeout = window.setTimeout(resolve, 1100);
          }),
        ])
      : Promise.resolve();

    void Promise.all([elapsed, fonts]).then(() => {
      if (!cancelled) setStage("fading");
    });

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.clearTimeout(fontTimeout);
    };
  }, [stage]);

  useEffect(() => {
    if (stage !== "fading") return;
    const timer = window.setTimeout(() => setStage("hidden"), 300);
    return () => window.clearTimeout(timer);
  }, [stage]);

  useEffect(() => {
    const before = previousPath.current;
    previousPath.current = pathname;
    if (!needsSettingsSplash(before, pathname, surface)) return;

    // Covers browser back/forward and programmatic router.push navigations.
    // Deferring a setState avoids synchronous effect state updates.
    const frame = window.requestAnimationFrame(() => setStage("short"));
    return () => window.cancelAnimationFrame(frame);
  }, [pathname, surface]);

  useEffect(() => {
    // Show immediately when a Settings boundary link is chosen, rather than
    // waiting for the destination client route to finish rendering.
    const onClick = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 ||
          event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      const link = target.closest("a[href]");
      if (!link || link.hasAttribute("download") ||
          (link.target && link.target !== "_self")) return;
      try {
        const url = new URL(link.href, window.location.href);
        if (!["http:", "https:"].includes(url.protocol) || url.origin !== window.location.origin) return;
        if (needsSettingsSplash(window.location.pathname, url.pathname, surface)) {
          setStage("short");
        }
      } catch {
        // Ignore invalid navigation URLs; never block navigation.
      }
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [surface]);

  if (stage === "hidden") return null;

  return (
    <div
      className={"mm-splash" + (stage === "fading" ? " mm-splash--out" : "")}
      role="status"
      aria-label="Loading MateMail"
      aria-hidden={stage === "fading" ? "true" : undefined}
    >
      <div className="mm-splash__ring" aria-hidden="true">
        <div className="mm-splash__track" />
        <div className="mm-splash__arc" />
        <img src="/assets/matemail-splash-mark.svg" alt="" width="96" height="96"
          className="mm-splash__mark" decoding="async" fetchPriority="high" />
      </div>
      <div className="mm-splash__wordmark">MateMail</div>
      <div className="mm-splash__caption">Loading workspace…</div>
    </div>
  );
}
