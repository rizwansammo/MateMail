"""
Mailbox-local conversation graph, using message headers rather than subject guesses.

The graph is constructed from *summaries* (no message bodies) collected from
the authenticated user's selectable conversation folders. Message-ID identifies
one message; References/In-Reply-To connect it to a conversation even when an
ancestor has moved out of the scanned folders. The subject alone never links
messages. No persistent DB index can go stale after an external IMAP client moves
mail, and no user-supplied Message-ID is trusted as an access token.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime

from .imap import MessageSummary

# Bound malformed or adversarial headers. Require an actual RFC-style addr-spec,
# so an empty References field or a plain subject cannot glue mail together.
_MESSAGE_ID = re.compile(r"<([^<>\s@]{1,128})@([^<>\s@]{1,190})>")
MAX_HEADER_IDS = 100

ROLE_PRIORITY = {"inbox": 0, "sent": 1, "archive": 2}


def message_ids(raw: str) -> tuple[str, ...]:
    """Extract up to 100 syntactically useful IDs, preserving local-part case."""
    if not raw:
        return ()
    found: list[str] = []
    seen: set[str] = set()
    for match in _MESSAGE_ID.finditer(raw):
        mid = f"<{match.group(1)}@{match.group(2).lower()}>"
        if mid not in seen:
            seen.add(mid)
            found.append(mid)
        if len(found) == MAX_HEADER_IDS:
            break
    return tuple(found)


def _timestamp(raw: str) -> float:
    try:
        value = parsedate_to_datetime(raw)
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.timestamp()
    except (TypeError, ValueError, OverflowError):
        pass
    return 0.0


def _copy_order(summary: MessageSummary, roles: dict[str, str]) -> tuple:
    return (
        ROLE_PRIORITY.get(roles.get(summary.folder, ""), 3),
        summary.folder.casefold(), summary.uid_validity, summary.uid,
    )


def _physical_key(summary: MessageSummary) -> tuple:
    # A UID is unique ONLY inside one folder AND its current UIDVALIDITY.
    return (summary.folder, summary.uid_validity, summary.uid)


def _duplicate_identity(summary: MessageSummary) -> tuple:
    """Do not deduplicate two conflicting messages that reused a Message-ID."""
    return (
        summary.from_address.casefold().strip(),
        " ".join(summary.subject.split()).casefold(),
        _timestamp(summary.date) if summary.date else None,
    )


@dataclass
class ConversationMessage:
    primary: MessageSummary
    copies: list[MessageSummary]
    references: tuple[str, ...]
    own_id: str

    @property
    def unread(self) -> bool:
        return any(not copy.seen for copy in self.copies)

    @property
    def flagged(self) -> bool:
        return any(copy.flagged for copy in self.copies)


@dataclass
class Conversation:
    id: str
    messages: list[ConversationMessage]
    latest: ConversationMessage
    unread_count: int
    flagged: bool
    has_inbox: bool
    participants: list[str]


def build_conversations(
    summaries: list[MessageSummary],
    roles: dict[str, str],
) -> list[Conversation]:
    """Connected components of the message-ID/reference graph.

    Duplicate physical results and actual copies across folders collapse into
    one logical message; copies retain their own (folder, UIDVALIDITY, UID).
    If two DIFFERENT messages reuse a Message-ID, that ID becomes ambiguous and
    cannot connect or deduplicate anything. Their other valid references can
    still connect them if they truly belong together.
    """
    physical: dict[tuple, MessageSummary] = {}
    for summary in summaries:
        physical.setdefault(_physical_key(summary), summary)

    duplicates: dict[tuple, list[MessageSummary]] = defaultdict(list)
    for summary in physical.values():
        ids = message_ids(summary.message_id)
        if ids:
            key = ("mid", ids[0], _duplicate_identity(summary))
        else:
            key = ("physical", *_physical_key(summary))
        duplicates[key].append(summary)

    records: list[ConversationMessage] = []
    for copies in duplicates.values():
        copies.sort(key=lambda item: _copy_order(item, roles))
        own = message_ids(copies[0].message_id)
        refs: list[str] = []
        seen_refs: set[str] = set()
        for copy in copies:
            for raw in [*copy.thread_references, copy.in_reply_to]:
                for mid in message_ids(raw):
                    if mid not in seen_refs:
                        seen_refs.add(mid)
                        refs.append(mid)
        records.append(
            ConversationMessage(
                primary=copies[0],
                copies=copies,
                references=tuple(refs),
                own_id=own[0] if own else "",
            )
        )

    # Reused Message-IDs that disagree about sender/subject/date must not be
    # treated as universal ancestors: an unrelated reply could otherwise glue
    # the conflicting conversations together.
    owners: dict[str, set[tuple]] = defaultdict(set)
    for record in records:
        if record.own_id:
            owners[record.own_id].add(_duplicate_identity(record.primary))
    ambiguous = {mid for mid, identities in owners.items() if len(identities) > 1}

    parent = list(range(len(records)))
    rank = [0] * len(records)

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a: int, b: int) -> None:
        ra, rb = root(a), root(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    by_header: dict[str, int] = {}
    for i, record in enumerate(records):
        keys = set(record.references)
        if record.own_id:
            keys.add(record.own_id)
        for mid in keys - ambiguous:
            if mid in by_header:
                join(i, by_header[mid])
            else:
                by_header[mid] = i

    components: dict[int, list[ConversationMessage]] = defaultdict(list)
    for i, record in enumerate(records):
        components[root(i)].append(record)

    conversations: list[Conversation] = []
    for messages in components.values():
        messages.sort(key=lambda rec: (
            _timestamp(rec.primary.date),
            rec.primary.folder.casefold(),
            rec.primary.uid_validity,
            rec.primary.uid,
        ))
        latest = messages[-1]
        # In normal replies References begins with the original root ID. When
        # the ancestor is not present, siblings still share this candidate.
        anchors = [
            next((ref for ref in rec.references if ref not in ambiguous), rec.own_id)
            for rec in messages
        ]
        seed = min((key for key in anchors if key and key not in ambiguous), default="")
        if not seed:
            # Headerless mail must never be grouped solely by matching subjects.
            first = messages[0].primary
            seed = f"{first.folder}\0{first.uid_validity}\0{first.uid}"
        thread_id = hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()
        participants: set[str] = set()
        for rec in messages:
            participants.update(
                address.strip().casefold()
                for address in [
                    rec.primary.from_address,
                    *rec.primary.to,
                    *rec.primary.cc,
                ]
                if address.strip()
            )
        conversations.append(
            Conversation(
                id=thread_id,
                messages=messages,
                latest=latest,
                unread_count=sum(rec.unread for rec in messages),
                flagged=any(rec.flagged for rec in messages),
                has_inbox=any(
                    roles.get(copy.folder) == "inbox"
                    for rec in messages for copy in rec.copies
                ),
                participants=sorted(participants),
            )
        )

    conversations.sort(key=lambda convo: (
        _timestamp(convo.latest.primary.date), convo.id,
    ), reverse=True)
    return conversations
