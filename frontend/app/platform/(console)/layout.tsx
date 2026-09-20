"use client";

import { PlatformShell } from "@/components/platform/shell";

/**
 * The console pages, inside the navigation shell.
 *
 * A route group, so `(console)` never appears in a URL: this file wraps
 * `/platform` and everything below it except the sign-in pages, which sit
 * outside the group precisely so they are not framed by navigation the
 * visitor has not earned yet.
 */
export default function ConsoleLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <PlatformShell>{children}</PlatformShell>;
}
