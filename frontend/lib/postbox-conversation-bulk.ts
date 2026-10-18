import type { ConversationDetail, ConversationSummary } from "./postbox-api";

export interface ConversationActionGroup {
  folder: string;
  uidValidity: number;
  uids: number[];
  sentOrigin: boolean;
}

export interface ConversationActionPlan {
  conversations: Array<{ id: string; subject: string; messageCount: number }>;
  conversationCount: number;
  messageCount: number;
  copyCount: number;
  groups: ConversationActionGroup[];
}

/**
 * Resolve actions against a fresh, complete server snapshot. A conversation
 * represents logical messages, but IMAP mutations must target every stored
 * copy by its own folder, UID and UIDVALIDITY.
 */
export function planConversationActions(
  selected: ConversationSummary[],
  details: ConversationDetail[],
  sentFolders: ReadonlySet<string>,
): ConversationActionPlan {
  if (!selected.length || selected.length !== details.length) {
    throw new Error("Select conversations and try again.");
  }

  const groups = new Map<string, ConversationActionGroup>();
  const identities = new Set<string>();
  const copyIdentities = new Set<string>();
  const conversations: ConversationActionPlan["conversations"] = [];
  let messageCount = 0;
  let copyCount = 0;

  for (let index = 0; index < selected.length; index++) {
    const summary = selected[index];
    const detail = details[index];
    if (identities.has(summary.id) || detail.id !== summary.id ||
        detail.message_count !== summary.message_count ||
        detail.messages.length !== detail.message_count ||
        detail.messages.length === 0) {
      throw new Error("A conversation has changed. Refresh before applying an action.");
    }
    identities.add(summary.id);

    const anchor = summary.latest;
    const anchorFound = detail.messages.some((member) => member.copies.some(
      (copy) => copy.folder === anchor.folder && copy.uid === anchor.uid &&
        copy.uid_validity === anchor.uid_validity,
    ));
    if (!anchorFound) {
      throw new Error("A conversation has changed. Refresh before applying an action.");
    }

    conversations.push({
      id: detail.id,
      subject: detail.subject || "(no subject)",
      messageCount: detail.message_count,
    });
    messageCount += detail.message_count;

    for (const member of detail.messages) {
      if (!member.copies.length) {
        throw new Error("A conversation has an incomplete message list. Refresh and try again.");
      }
      for (const copy of member.copies) {
        if (!copy.folder || !Number.isSafeInteger(copy.uid) || copy.uid < 1 ||
            !Number.isSafeInteger(copy.uid_validity) || copy.uid_validity < 1) {
          throw new Error("A conversation contains an invalid message reference.");
        }
        const sentOrigin = Boolean(member.sent_origin) || sentFolders.has(copy.folder) ||
          copy.folder.toUpperCase() === "SENT";
        const identity = JSON.stringify([copy.folder, copy.uid_validity, copy.uid]);
        if (copyIdentities.has(identity)) continue;
        copyIdentities.add(identity);
        // Preserve Sent semantics when applying read/archive/spam actions.
        const key = JSON.stringify([copy.folder, copy.uid_validity, sentOrigin]);
        let group = groups.get(key);
        if (!group) {
          group = { folder: copy.folder, uidValidity: copy.uid_validity, uids: [], sentOrigin };
          groups.set(key, group);
        }
        group.uids.push(copy.uid);
        copyCount++;
      }
    }
  }

  return { conversations, conversationCount: conversations.length, messageCount,
    copyCount, groups: [...groups.values()] };
}
