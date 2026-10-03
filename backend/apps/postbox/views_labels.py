"""PostBox virtual labels: mailbox-owned metadata, never an IMAP COPY.

Assignments use the same stable identity as restore provenance, so moving a
message between IMAP folders does not detach its labels. An external client
can still delete or replace a message; the bounded label view scans live IMAP
headers and never pretends a stale assignment is a delivered message.
"""
import uuid
import hashlib
from apps.security import ratelimit
from apps.security.limits import POSTBOX_SEARCH_PER_MAILBOX
from rest_framework.exceptions import Throttled
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.response import Response

from . import imap
from .models import MailLabel, MessageLabel
from .views_mail import (
    PostBoxView, _fetch_summaries_chunked, _global_message_sort_key,
    _message_provenance_key, _summary_payload,
    _assert_uid_validity,
)

MAX_LABEL_VIEW_MESSAGES = 5000
MAX_LABEL_VIEW_FOLDERS = 40


def virtual_label_key(summary):
    """A MOVE-stable identity resistant to reused or forged Message-IDs."""
    source = "\x1f".join([
        _message_provenance_key(summary),
        summary.from_address.strip().casefold(),
        summary.subject.strip(),
        summary.date.strip(),
        str(summary.size),
    ])
    return "vlabel:" + hashlib.sha256(
        source.encode("utf-8", "replace")
    ).hexdigest()


def labels_for_summaries(mailbox, summaries):
    """One DB lookup for all visible messages, scoped to the active mailbox."""
    keys = {virtual_label_key(item) for item in summaries}
    mapping = {key: [] for key in keys}
    if keys:
        for entry in MessageLabel.objects.filter(
            label__mailbox=mailbox, message_key__in=keys
        ).select_related("label").order_by("label__name"):
            mapping[entry.message_key].append({
                "id": str(entry.label_id),
                "name": entry.label.name,
            })
    return mapping


def decorate_summaries(mailbox, summaries):
    mapping = labels_for_summaries(mailbox, summaries)
    return [
        {**_summary_payload(item), "labels": mapping[virtual_label_key(item)]}
        for item in summaries
    ]


def _name(raw):
    if not isinstance(raw, str):
        raise serializers.ValidationError({"name": "Enter a label name."})
    name = raw.strip()
    if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
        raise serializers.ValidationError({
            "name": "Use a label name of 1–80 characters without control characters."
        })
    return name


def _payload(label):
    return {
        "id": str(label.id),
        "name": label.name,
        "count": label.assignments.count(),
    }


class LabelListView(PostBoxView):
    def get(self, request):
        from django.db.models import Count
        labels = MailLabel.objects.for_mailbox(self.mailbox).annotate(
            assignment_count=Count("assignments")
        )
        return Response({"results": [
            {"id": str(item.id), "name": item.name, "count": item.assignment_count}
            for item in labels
        ]})

    def post(self, request):
        name = _name(request.data.get("name"))
        if MailLabel.objects.for_mailbox(self.mailbox).filter(name__iexact=name).exists():
            return Response({"name": "A label with that name already exists."}, status=400)
        try:
            with transaction.atomic():
                label = MailLabel.objects.create(mailbox=self.mailbox, name=name)
        except IntegrityError:
            return Response({"name": "A label with that name already exists."}, status=400)
        return Response(_payload(label), status=201)


class LabelDetailView(PostBoxView):
    def _get(self, pk):
        return MailLabel.objects.for_mailbox(self.mailbox).filter(pk=pk).first()

    def patch(self, request, pk):
        label = self._get(pk)
        if label is None:
            return Response({"detail": "Label not found."}, status=404)
        name = _name(request.data.get("name"))
        if MailLabel.objects.for_mailbox(self.mailbox).filter(
            name__iexact=name
        ).exclude(pk=pk).exists():
            return Response({"name": "A label with that name already exists."}, status=400)
        label.name = name
        try:
            with transaction.atomic():
                label.save(update_fields=["name"])
        except IntegrityError:
            return Response({"name": "A label with that name already exists."}, status=400)
        return Response(_payload(label))

    def delete(self, request, pk):
        label = self._get(pk)
        if label is None:
            return Response({"detail": "Label not found."}, status=404)
        # Only the virtual relationships are removed, never an IMAP message.
        label.delete()
        return Response(status=204)


class LabelAssignmentView(PostBoxView):
    """Add/remove ONE label from real, UIDVALIDITY-verified messages."""

    def post(self, request):
        try:
            label_id = uuid.UUID(str(request.data.get("label_id", "")))
        except (ValueError, AttributeError):
            return Response({"label_id": "Choose a label."}, status=400)
        label = MailLabel.objects.for_mailbox(self.mailbox).filter(pk=label_id).first()
        if label is None:
            return Response({"detail": "Label not found."}, status=404)

        folder = request.data.get("folder")
        uids = request.data.get("uids")
        validity = request.data.get("uid_validity")
        remove = request.data.get("remove", False)
        if (not isinstance(folder, str) or not folder or not isinstance(uids, list)
                or not uids or len(uids) > 500 or type(remove) is not bool
                or not all(type(uid) is int and uid > 0 for uid in uids)):
            return Response({"detail": "Choose up to 500 valid messages."}, status=400)
        # No stale UID can silently assign a label to a different message.
        if type(validity) is not int or validity < 1:
            return Response({"uid_validity": "Refresh the folder and try again."}, status=400)

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            if info.uid_validity != validity:
                return Response({"detail": "Folder changed. Refresh and try again."}, status=409)
            summaries = connection.fetch_summaries(list(dict.fromkeys(uids)))
            if {item.uid for item in summaries} != set(uids):
                return Response({"detail": "Some messages no longer exist. Refresh."}, status=409)

        keys = {virtual_label_key(item) for item in summaries}
        if remove:
            MessageLabel.objects.filter(label=label, message_key__in=keys).delete()
        else:
            MessageLabel.objects.bulk_create(
                [MessageLabel(label=label, message_key=key) for key in keys],
                ignore_conflicts=True,
            )
        return Response({"label": _payload(label), "count": len(keys)})


class LabelMessagesView(PostBoxView):
    """Resolve virtual membership against the live mail store.

    Bounded scans are intentional: never return a label as if it contained
    messages that were removed in Outlook, nor allocate unbounded memory.
    A future index can replace this scan without changing the API.
    """

    def get(self, request, pk):
        label = MailLabel.objects.for_mailbox(self.mailbox).filter(pk=pk).first()
        if label is None:
            return Response({"detail": "Label not found."}, status=404)
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 30))
        except (ValueError, TypeError):
            return Response({"detail": "Invalid pagination."}, status=400)
        if page < 1 or page_size < 1 or page_size > 100:
            return Response({"detail": "Invalid pagination."}, status=400)

        keys = set(label.assignments.values_list("message_key", flat=True))
        if not keys:
            return Response({
                "folder": "INBOX", "scope": "label", "page": page,
                "page_size": page_size, "total": 0, "has_next": False,
                "results": [],
            })

        decision = ratelimit.hit(
            POSTBOX_SEARCH_PER_MAILBOX.bucket, str(self.mailbox.pk),
            limit=POSTBOX_SEARCH_PER_MAILBOX.limit,
            window=POSTBOX_SEARCH_PER_MAILBOX.window,
        )
        if not decision.allowed:
            raise Throttled(
                wait=decision.retry_after,
                detail="Too many label searches. Try again shortly.",
            )

        collected = []
        seen_keys = set()
        with imap.open_mailbox(self.mailbox.email) as connection:
            folders = [
                item for item in connection.list_folders()
                if item.selectable and item.role not in {"junk", "trash"}
            ]
            if len(folders) > MAX_LABEL_VIEW_FOLDERS:
                return Response({
                    "detail": "Too many folders to show labels safely."
                }, status=400)
            scanned = 0
            # Inbox first: if an external client duplicated a message, prefer
            # its Inbox occurrence and show it only once in the virtual view.
            folders.sort(key=lambda f: (f.role != "inbox", f.name))
            for item in folders:
                connection.select(item.name, readonly=True)
                uids = connection.search_uids(["ALL"])
                scanned += len(uids)
                if scanned > MAX_LABEL_VIEW_MESSAGES:
                    return Response({
                        "detail": "This mailbox is too large for label view yet."
                    }, status=400)
                for summary in _fetch_summaries_chunked(connection, uids):
                    key = virtual_label_key(summary)
                    if key in keys and key not in seen_keys:
                        seen_keys.add(key)
                        collected.append(summary)

        q = (request.query_params.get("q") or "").strip().casefold()
        if q:
            collected = [
                item for item in collected
                if q in item.subject.casefold() or q in item.from_address.casefold()
            ]
        if request.query_params.get("unread") == "true":
            collected = [item for item in collected if not item.seen]
        if request.query_params.get("starred") == "true":
            collected = [item for item in collected if item.flagged]
        newest = request.query_params.get("sort") != "oldest"
        collected.sort(key=_global_message_sort_key, reverse=newest)
        start = (page - 1) * page_size
        return Response({
            "folder": "INBOX", "scope": "label", "page": page,
            "page_size": page_size, "total": len(collected),
            "has_next": start + page_size < len(collected),
            "results": decorate_summaries(
                self.mailbox, collected[start:start + page_size],
            ),
        })
