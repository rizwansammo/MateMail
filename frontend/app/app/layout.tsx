"use client";

import type { ReactNode } from "react";
import AstraWorkspaceShell from "@/components/workspace/astra-shell";

/** Hub-only visual migration: all existing /app route components remain mounted. */
export default function AppLayout({ children }: { children: ReactNode }) {
  return <AstraWorkspaceShell>{children}</AstraWorkspaceShell>;
}
