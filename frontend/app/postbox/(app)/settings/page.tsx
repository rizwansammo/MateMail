"use client";

import { Suspense } from "react";

import LegacySettingsPage from "@/components/postbox/legacy-settings";
import PremiumSettings from "@/components/postbox/premium-settings";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

export default function SettingsPage() {
  return (
    <div className="pb-scroll h-full">
      {IS_NETAMATE_EMAIL ? (
        <LegacySettingsPage />
      ) : (
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-sm pb-subtle">
              Loading settings…
            </div>
          }
        >
          <PremiumSettings />
        </Suspense>
      )}
    </div>
  );
}
