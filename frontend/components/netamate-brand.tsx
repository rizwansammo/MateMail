import Image from "next/image";

import { NETAMATE_LOGO_SRC } from "@/lib/brand";

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
      <div className="min-w-0">
        <p className={compact ? "nm-wordmark text-sm leading-none" : "nm-wordmark text-[17px] leading-none"}>
          NetaMate Email
        </p>
        <p className={compact ? "nm-surface-label mt-1 text-[9px]" : "nm-surface-label mt-1 text-[10px]"}>
          {surface}
        </p>
      </div>
    </div>
  );
}
