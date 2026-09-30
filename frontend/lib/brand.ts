/**
 * Dedicated-host build selector.
 *
 * IMPORTANT: this flag may control hostname/access behavior only. NetaMate's
 * dedicated MailAdmin/PostBox endpoints intentionally use the same MateMail
 * visual identity, components, titles and browser icons as the canonical
 * product. Do not use this flag to fork product UI or branding.
 */
export const BRAND_VARIANT =
  process.env.NEXT_PUBLIC_BRAND_VARIANT ?? "matemail";

export const IS_NETAMATE_EMAIL = BRAND_VARIANT === "netamate-email";
