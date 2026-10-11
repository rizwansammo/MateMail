"use client";

import type { ReactNode } from "react";
import AstraWorkspaceShell from "@/components/workspace/astra-shell";
import { MateMailSplash } from "@/components/matemail-splash";

/** Hub-only visual migration: all existing /app route components remain mounted. */
export default function AppLayout({ children }: { children: ReactNode }) {
  return <><MateMailSplash surface="hub" /><AstraWorkspaceShell>{children}</AstraWorkspaceShell></>;
}
