import type { Metadata } from "next";

import { PostBoxProvider } from "@/contexts/postbox-context";

/**
 * Everything under /postbox.
 *
 * The provider wraps the sign-in page too: it is what knows whether somebody
 * already has a session, and bouncing them straight to their mail is better
 * than showing a form they do not need.
 */
export const metadata: Metadata = {
  title: {
    // The root MateMail metadata template appends " | MateMail".
    // Keep this segment to the surface name so every host reads
    // "PostBox | MateMail" without a brand-specific suffix.
    default: "PostBox",
    template: "%s · PostBox",
  },
  description:
    "Business email, beautifully organised. MateMail PostBox by NetaMate Solutions.",
  icons: {
    icon: [
      {
        url: "/postbox/favicon?v=3",
        type: "image/svg+xml",
        sizes: "any",
      },
    ],
    shortcut: [
      {
        url: "/postbox/favicon?v=3",
        type: "image/svg+xml",
        sizes: "any",
      },
    ],
  },
  // A mailbox is not a landing page.
  robots: { index: false, follow: false },
};

export default function PostBoxLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <PostBoxProvider>{children}</PostBoxProvider>;
}
