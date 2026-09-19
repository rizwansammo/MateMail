import Image from "next/image";
import { cn } from "@/lib/utils";

/**
 * The MateMail logo, in the square tile the brand lockups are built around.
 *
 * Every placement previously drew a `ShieldCheck` glyph — or, on the invite
 * page, a letter "M" — inside a coloured tile. Those were placeholders, and
 * they had drifted to three different colours. This is the one mark.
 *
 * The tile is white in every context because the logo is a single navy
 * (#103359) with a transparent ground: on the dark sidebars it would otherwise
 * be all but invisible. Callers that need to signal something about the
 * surrounding surface do it with a ring rather than a fill, so the mark itself
 * never changes colour.
 *
 * `alt` is empty by design. Every placement sits beside the word "MateMail",
 * so a screen reader announcing the name twice would be noise, and the tile is
 * decorative once the text is there.
 */

/** The asset is 512x380; the tile must not distort it. */
const ASPECT = 380 / 512;

/** How much of the tile the mark occupies, leaving it visually inset. */
const INSET = 0.72;

export function BrandMark({
  size = 32,
  className,
  preload = false,
}: {
  /** Tile edge in pixels. */
  size?: number;
  className?: string;
  /**
   * Next.js 16 renamed `priority` to `preload`. Set it for a mark that is in
   * the first paint — a sidebar header — and leave it off elsewhere.
   */
  preload?: boolean;
}) {
  const width = Math.round(size * INSET);

  return (
    <span
      className={cn("inline-grid shrink-0 place-items-center bg-white", className)}
      style={{ width: size, height: size }}
    >
      <Image
        src="/matemail-logo.png"
        alt=""
        width={width}
        height={Math.round(width * ASPECT)}
        preload={preload}
      />
    </span>
  );
}
