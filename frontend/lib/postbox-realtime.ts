/**
 * Browser-side PostBox mailbox-change bus.
 *
 * One tab dispatches a local DOM event and mirrors the content-free wake-up to
 * its sibling tabs with BroadcastChannel. The server's SSE stream uses the
 * same shape, so every consumer has one event to listen to.
 */
export const POSTBOX_MAILBOX_EVENT = "postbox:mailbox-changed";
const CHANNEL_NAME = "matemail-postbox-mailbox-v1";
const STORAGE_KEY = "matemail:postbox:mailbox-change";

export interface PostBoxMailboxChange {
  event_id: string;
  kind: string;
  folder?: string;
  uid_validity?: number;
  uid?: number;
  action?: string;
}

let channel: BroadcastChannel | null = null;
const seenIds: string[] = [];
const seen = new Set<string>();

function remember(eventId: string): boolean {
  if (seen.has(eventId)) return false;
  seen.add(eventId);
  seenIds.push(eventId);
  if (seenIds.length > 200) {
    const oldest = seenIds.shift();
    if (oldest) seen.delete(oldest);
  }
  return true;
}

function eventId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function localDispatch(change: PostBoxMailboxChange) {
  if (typeof window === "undefined" || !remember(change.event_id)) return;
  window.dispatchEvent(
    new CustomEvent<PostBoxMailboxChange>(POSTBOX_MAILBOX_EVENT, { detail: change }),
  );
}

function getChannel(): BroadcastChannel | null {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") {
    return null;
  }
  if (!channel) channel = new BroadcastChannel(CHANNEL_NAME);
  return channel;
}

export function announcePostBoxMailboxChange(
  partial: Omit<PostBoxMailboxChange, "event_id"> & { event_id?: string },
  broadcast = true,
): PostBoxMailboxChange {
  const change: PostBoxMailboxChange = {
    ...partial,
    event_id: partial.event_id || eventId(),
  };
  localDispatch(change);
  if (!broadcast || typeof window === "undefined") return change;

  const broadcastChannel = getChannel();
  if (broadcastChannel) {
    broadcastChannel.postMessage(change);
  } else {
    // Older browsers still get cross-tab sync through the storage event.
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ nonce: eventId(), change }),
      );
    } catch {
      // Private browsing/storage denial must not affect mailbox actions.
    }
  }
  return change;
}

export function installPostBoxCrossTabSync(): () => void {
  if (typeof window === "undefined") return () => {};

  const broadcastChannel = getChannel();
  const onMessage = (event: MessageEvent<PostBoxMailboxChange>) => {
    if (event.data?.event_id) localDispatch(event.data);
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY || !event.newValue) return;
    try {
      const parsed = JSON.parse(event.newValue) as { change?: PostBoxMailboxChange };
      if (parsed.change?.event_id) localDispatch(parsed.change);
    } catch {
      // Ignore malformed cross-tab state; it is only a wake-up hint.
    }
  };

  if (broadcastChannel) broadcastChannel.addEventListener("message", onMessage);
  else window.addEventListener("storage", onStorage);

  return () => {
    if (broadcastChannel) broadcastChannel.removeEventListener("message", onMessage);
    else window.removeEventListener("storage", onStorage);
  };
}
