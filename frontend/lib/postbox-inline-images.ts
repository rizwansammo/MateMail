import { postbox, type MessageDetail } from "@/lib/postbox-api";

/**
 * A browser does not understand RFC 2392 cid: URLs by itself. Map only CIDs
 * that the server reported as safe, previewable image parts to authenticated
 * same-origin preview URLs. The message HTML itself was already sanitised by
 * the backend; this function only resolves its inert inline-image references.
 */
export function resolveInlineImageReferences(detail: MessageDetail): string {
  if (!detail.html || !detail.html.toLowerCase().includes("cid:")) {
    return detail.html;
  }

  const inlineImages = new Map(
    detail.attachments
      .filter(
        (attachment) =>
          attachment.content_id &&
          attachment.previewable &&
          attachment.content_type.toLowerCase().startsWith("image/"),
      )
      .map((attachment) => [
        attachment.content_id.trim().replace(/^<|>$/g, "").toLowerCase(),
        postbox.attachmentPreviewUrl(
          detail.folder,
          detail.uid,
          attachment.part_id,
          detail.uid_validity,
        ),
      ]),
  );

  if (inlineImages.size === 0) return detail.html;

  return detail.html.replace(
    /(\bsrc\s*=\s*["'])cid:([^"']+)(["'])/gi,
    (match, prefix: string, rawCid: string, suffix: string) => {
      let cid = rawCid.trim();
      try {
        cid = decodeURIComponent(cid);
      } catch {
        // A malformed percent escape is just a CID that will not match.
      }
      const url = inlineImages.get(
        cid.replace(/^<|>$/g, "").toLowerCase(),
      );
      return url ? `${prefix}${url}${suffix}` : match;
    },
  );
}

