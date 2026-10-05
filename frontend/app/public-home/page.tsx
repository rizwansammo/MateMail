import type { Metadata } from "next";
import { MateMailPublicHome } from "@/components/public-home/homepage";

export const metadata: Metadata = {
  title: {
    absolute: "MateMail | Business Email by NetaMate Solutions",
  },
  description:
    "MateMail is business email by NetaMate Solutions, with Workspace administration and PostBox webmail for custom-domain email.",
  icons: {
    icon: "/assets/matemail-mark.svg",
    shortcut: "/assets/matemail-mark.svg",
  },
};

export default function PublicHomePage() {
  return <MateMailPublicHome />;
}
