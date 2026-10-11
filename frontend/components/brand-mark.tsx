import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * Canonical MateMail Hub mark.
 *
 * Identical square dimensions for the official 05B Light and Dark variants.
 * The active product appearance determines which mark is shown.
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
    <span className={cn("mm-official-brand", className)}
      style={{ width: size, height: size }} aria-hidden="true">
      <Image src="/assets/matemail-mark.svg" alt="" width={size}
        height={size} preload={preload} className="mm-official-brand-default" />
      <Image src="/assets/matemail-mark-black.svg" alt="" width={size}
        height={size} className="mm-official-brand-inverse" />
    </span>
  );
}
