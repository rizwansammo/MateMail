import type { Metadata } from "next";

import { PlatformAuthProvider } from "@/contexts/platform-auth-context";
import { PlatformThemeProvider } from "@/components/platform/theme";

/**
 * Everything under /platform.
 *
 * The providers sit here rather than in the console group because the sign-in
 * and password-reset pages need them too: the login page reads the auth
 * context to bounce an operator who already has a session, and every page
 * needs the theme.
 *
 * The console shell — sidebar, header, search — lives one level down in
 * `(console)/layout.tsx`, so the sign-in pages are not wrapped in navigation
 * for a console the visitor cannot yet open.
 */
export const metadata: Metadata = {
  title: {
    default: "MateMail Platform Console",
    template: "%s · MateMail Platform",
  },
  description: "Operational control for the MateMail platform.",
  // Nothing under here should ever appear in a search index. It is an internal
  // operations surface, and its login page is not a landing page.
  robots: { index: false, follow: false },
};

export default function PlatformLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <PlatformThemeProvider>
      <PlatformAuthProvider>{children}</PlatformAuthProvider>
    </PlatformThemeProvider>
  );
}
