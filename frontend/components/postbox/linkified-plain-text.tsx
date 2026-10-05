import type { ReactNode } from "react";

/**
 * Make HTTP(S) URLs in a plain-text email clickable without converting the
 * message into HTML.
 *
 * This deliberately returns ordinary React text nodes around <a> elements.
 * Newlines, spaces and every non-URL character remain exactly as received;
 * the caller's <pre className="whitespace-pre-wrap"> keeps the original
 * message layout. No HTML parser or dangerouslySetInnerHTML is involved.
 */
const HTTP_URL = /https?:\/\/[^\s<>"']+/gi;
const SIMPLE_TRAILING_PUNCTUATION = new Set([".", ",", ";", ":", "!", "?"]);

function count(value: string, character: string): number {
  return value.split(character).length - 1;
}

function splitTrailingPunctuation(candidate: string): {
  href: string;
  trailing: string;
} {
  let href = candidate;
  let trailing = "";

  // Sentence punctuation immediately after a URL is prose, not part of the
  // destination. Moving it into a following text node preserves the source
  // exactly while avoiding a broken link.
  while (href && SIMPLE_TRAILING_PUNCTUATION.has(href[href.length - 1])) {
    trailing = href[href.length - 1] + trailing;
    href = href.slice(0, -1);
  }

  // Keep balanced closing punctuation inside a URL, but peel off an unmatched
  // closing bracket from ordinary prose: "See https://example.test/foo)."
  for (const [open, close] of [
    ["(", ")"],
    ["[", "]"],
    ["{", "}"],
  ] as const) {
    while (
      href.endsWith(close) &&
      count(href, close) > count(href, open)
    ) {
      trailing = close + trailing;
      href = href.slice(0, -1);
    }
  }

  return { href, trailing };
}

export function LinkifiedPlainText({ text }: { text: string }) {
  if (!text) return null;

  const output: ReactNode[] = [];
  let cursor = 0;
  let key = 0;

  for (const match of text.matchAll(HTTP_URL)) {
    const start = match.index ?? 0;
    const raw = match[0];

    // The untouched text before the URL is emitted as one literal text node.
    if (start > cursor) output.push(text.slice(cursor, start));

    const { href, trailing } = splitTrailingPunctuation(raw);
    if (href) {
      output.push(
        <a
          key={"url-" + key++}
          className="pb-plain-text-link"
          href={href}
          target="_blank"
          rel="noopener noreferrer nofollow"
        >
          {href}
        </a>,
      );
    } else {
      output.push(raw);
    }

    if (trailing) output.push(trailing);
    cursor = start + raw.length;
  }

  // And the untouched remainder follows exactly as received.
  if (cursor < text.length) output.push(text.slice(cursor));

  return <>{output}</>;
}
