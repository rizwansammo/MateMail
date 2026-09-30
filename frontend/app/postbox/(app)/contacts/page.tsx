"use client";

import LegacyContactsPage from "@/components/postbox/legacy-contacts";
import PremiumContacts from "@/components/postbox/premium-contacts";
import { IS_NETAMATE_EMAIL } from "@/lib/brand";

export default function ContactsPage() {
  return (
    <div className="pb-scroll h-full">
      IS_NETAMATE_EMAIL ? <LegacyContactsPage /> : <PremiumContacts />
    </div>
  );
}
