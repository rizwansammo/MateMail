import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import localFont from "next/font/local";
import { headers } from "next/headers";
import { AuthProvider } from "@/contexts/auth-context";
import "./globals.css";
import "./app/portal-premium.css";
import "./app/astra-shell.css";
import "./app/astra-resources.css";
import "./app/astra-collaboration.css";
import "./app/astra-routing.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

// The verified prototype Hemi Head Bold file. Next.js serves this font as a
// versioned same-origin /_next/static asset, independent of /postbox routing.
const mateMailHubFont = localFont({
  src: "../public/assets/HemiHead-Bold.otf",
  variable: "--font-matemail-hub",
  display: "block",
  preload: false,
  weight: "700",
  style: "normal",
});

export const metadata: Metadata = {
  title: {
    default: "MateMail · Organization Hub",
    template: "%s | MateMail",
  },
  description:
    "Host domain-based inboxes, manage DNS health, monitor deliverability, and give your team MateMail PostBox.",
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_APP_URL ?? "https://matemail.pro",
  ),
  icons: {
    icon: "/assets/matemail-mark.svg",
    shortcut: "/assets/matemail-mark.svg",
  },
};

/**
 * Every route renders per request.
 *
 * The CSP nonce in `middleware.ts` is generated per request, and Next.js can
 * only stamp it onto script tags for a route it renders at request time. A
 * statically prerendered page has its HTML — and its inline hydration scripts
 * — fixed at build time, with no nonce in them. Serving those under a nonce
 * policy blocks the scripts and the page renders as a dead shell, which is the
 * P2.5 failure with the protection now actively working against us.
 *
 * Verified rather than assumed: before this line the production server emitted
 * 19 script tags and 0 nonce attributes.
 *
 * The cost is the static optimisation for the marketing page. Everything under
 * /app and /admin is an authenticated client-rendered dashboard that was never
 * meaningfully static, and the pages remain cheap to render — nothing here
 * fetches during SSR.
 */
export const dynamic = "force-dynamic";

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Reading the request headers is what tells Next.js this tree is dynamic and
  // makes the nonce available to its script emission.
  await headers();

  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${mateMailHubFont.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
