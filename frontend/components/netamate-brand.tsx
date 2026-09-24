import Image from "next/image";

import { NETAMATE_LOGO_SRC } from "@/lib/brand";

/**
 * The NetaMate lockup, which is not the same shape on both surfaces.
 *
 * PostBox is a two-line WORDMARK — "NetaMate" over "PostBox", both in the
 * brand face, the second smaller. It reads as one name in two lines, and the
 * pair is sized so the stack matches the logo tile's height.
 *
 * MailAdmin keeps the original name-plus-label form: a wordmark with a spaced
 * uppercase caption underneath. That caption is a category label, and it is
 * deliberately NOT applied to PostBox, where it made "POSTBOX" read as a
 * separate tag stapled beneath a different product's name.
 *
 * The surfaces are branched explicitly rather than styled by a shared prop,
 * because they are two different pieces of typography that happen to share a
 * logo — and a change to one should not silently reshape the other.
 */
export function NetaMateBrand({
  surface,
  compact = false,
  preload = false,
}: {
  surface: "PostBox" | "MailAdmin";
  compact?: boolean;
  preload?: boolean;
}) {
  const markSize = compact ? 30 : 38;

  return (
    <div className="nm-brand-lockup flex min-w-0 items-center gap-3">
      <span
        className="nm-logo-tile inline-grid shrink-0 place-items-center"
        style={{ width: markSize, height: markSize }}
      >
        <Image
          src={NETAMATE_LOGO_SRC}
          alt=""
          width={markSize}
          height={markSize}
          preload={preload}
          className="h-full w-full object-contain"
        />
      </span>

      {surface === "PostBox" ? (
        // Two lines of one wordmark. `nm-wordmark-stack` sets a line-height
        // pair whose total is the tile height, so the block is optically
        // centred against the mark without hand-tuned margins.
        <div className="nm-wordmark-stack min-w-0">
          <p className={compact ? "nm-wordmark text-[15px]" : "nm-wordmark text-[18px]"}>
            NetaMate
          </p>
          <p
            className={
              compact
                ? "nm-wordmark nm-wordmark-sub text-[12px]"
                : "nm-wordmark nm-wordmark-sub text-[14px]"
            }
          >
            PostBox
          </p>
        </div>
      ) : (
        <div className="min-w-0">
          <p
            className={
              compact
                ? "nm-wordmark text-sm leading-none"
                : "nm-wordmark text-[17px] leading-none"
            }
          >
            NetaMate Email
          </p>
          <p
            className={
              compact
                ? "nm-surface-label mt-1 text-[9px]"
                : "nm-surface-label mt-1 text-[10px]"
            }
          >
            {surface}
          </p>
        </div>
      )}
    </div>
  );
}
