"use client";

import { Suspense } from "react";

import PremiumSettings from "@/components/postbox/premium-settings";

export default function SettingsPage() {
  return (
    <div className="pb-scroll h-full">
      <Suspense
        fallback={
          <div className="flex h-full items-center justify-center text-sm pb-subtle">
            Loading settings…
          </div>
        }
      >
        <PremiumSettings />
      </Suspense>
    </div>
  );
}
