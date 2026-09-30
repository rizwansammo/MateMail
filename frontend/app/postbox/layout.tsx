import type { Metadata } from "next";

import { PostBoxProvider } from "@/contexts/postbox-context";
import { IS_NETAMATE_EMAIL, NETAMATE_LOGO_SRC } from "@/lib/brand";

/**
 * Everything under /postbox.
 *
 * The provider wraps the sign-in page too: it is what knows whether somebody
 * already has a session, and bouncing them straight to their mail is better
 * than showing a form they do not need.
 */
export const metadata: Metadata = IS_NETAMATE_EMAIL
  ? {
      title: {
        // The root NetaMate metadata template appends "· NetaMate Email".
        // Keep this segment to the surface name to avoid a duplicated suffix.
        default: "PostBox",
        template: "%s · NetaMate Email PostBox",
      },
      description: "Private NetaMate Solutions mailbox access.",
      icons: {
        icon: NETAMATE_LOGO_SRC,
        shortcut: NETAMATE_LOGO_SRC,
        apple: NETAMATE_LOGO_SRC,
      },
      robots: { index: false, follow: false },
    }
  : {
      title: {
        // The root MateMail metadata template appends " | MateMail".
        // Keep this segment to the surface name so the browser tab reads
        // "PostBox | MateMail" rather than duplicating the product name.
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
