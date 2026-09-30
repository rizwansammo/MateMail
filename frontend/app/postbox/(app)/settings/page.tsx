"use client";

import LegacySettingsPage from "@/components/postbox/legacy-settings";
import PremiumSettings from "@/components/postbox/premium-settings";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

export default function SettingsPage() {
  return IS_NETAMATE_EMAIL ? <LegacySettingsPage /> : <PremiumSettings />;
}
