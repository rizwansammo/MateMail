export type MateMailSurface = "hub" | "postbox";

export function isSettingsRoute(pathname: string, surface: MateMailSurface): boolean {
  const root = surface === "postbox" ? "/postbox/settings" : "/app/settings";
  return pathname === root || pathname.startsWith(root + "/");
}

/**
 * Only transitions across the Settings boundary deserve the short splash.
 * Query strings (folders, messages, individual setting sections) are ignored.
 */
export function needsSettingsSplash(
  previousPath: string,
  nextPath: string,
  surface: MateMailSurface,
): boolean {
  const base = surface === "postbox" ? "/postbox" : "/app";
  const inSurface = (path: string) => path === base || path.startsWith(base + "/");
  if (!inSurface(previousPath) || !inSurface(nextPath) || previousPath === nextPath) return false;
  return isSettingsRoute(previousPath, surface) !== isSettingsRoute(nextPath, surface);
}
