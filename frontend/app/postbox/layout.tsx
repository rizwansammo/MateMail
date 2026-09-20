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
    default: "MateMail PostBox",
    template: "%s · MateMail PostBox",
  },
  description: "Business email, beautifully organised. MateMail PostBox by NetaMate Solutions.",
  // A mailbox is not a landing page.
  robots: { index: false, follow: false },
};

export default function PostBoxLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <PostBoxProvider>{children}</PostBoxProvider>;
}
