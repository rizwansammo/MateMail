import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * Canonical MateMail Hub mark.
 *
 * The SVG is the same square mark used by the approved Portal prototype and
 * by the browser favicon. It already contains its navy background, so callers
 * must not wrap it in the legacy white logo tile.
 */
export function BrandMark({
  size = 32,
  className,
  preload = false,
}: {
  size?: number;
  className?: string;
  preload?: boolean;
}) {
  return (
    <Image
      src="/assets/matemail-mark.svg"
      alt=""
      width={size}
      height={size}
      preload={preload}
      className={cn("shrink-0", className)}
    />
  );
}
