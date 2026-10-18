import assert from 'node:assert/strict';
import test from 'node:test';
import {planConversationActions} from '../lib/postbox-conversation-bulk.ts';

const ref = (folder, uid, uid_validity = 8) => ({folder, uid, uid_validity, seen: false, flagged: false});
const member = (uid, folder, copies, sent_origin = false) => ({
  uid, folder, uid_validity: 8, sent_origin, copies,
});
const summary = (id, count, uid = 1, folder = 'INBOX') => ({
  id, message_count: count, latest: {uid, uid_validity: 8, folder}, subject: 'Example',
});
const detail = (id, members) => ({
  id, subject: 'Example', message_count: members.length, messages: members,
});

test('counts logical messages separately from stored copies and groups by folder', () => {
  const s = summary('a', 2);
  const d = detail('a', [
    member(1, 'INBOX', [ref('INBOX', 1)]),
    member(4, 'Sent', [ref('Sent', 4), ref('Archive', 10)], true),
  ]);
  const plan = planConversationActions([s], [d], new Set(['Sent']));
  assert.equal(plan.messageCount, 2);
  assert.equal(plan.copyCount, 3);
  assert.equal(plan.conversationCount, 1);
  assert.deepEqual(plan.groups.map(({folder, uids, sentOrigin}) => [folder, uids, sentOrigin]), [
    ['INBOX', [1], false], ['Sent', [4], true], ['Archive', [10], true],
  ]);
});

test('groups multiple threads by immutable folder and UIDVALIDITY', () => {
  const plan = planConversationActions(
    [summary('a', 1), summary('b', 1, 2)],
    [detail('a', [member(1, 'INBOX', [ref('INBOX', 1)])]),
      detail('b', [member(2, 'INBOX', [ref('INBOX', 2)])])],
    new Set(),
  );
  assert.equal(plan.conversationCount, 2);
  assert.deepEqual(plan.groups[0].uids, [1, 2]);
});

test('rejects changed counts, swapped threads, absent anchors and invalid copies', () => {
  const s = summary('a', 2);
  const d = detail('a', [member(1, 'INBOX', [ref('INBOX', 1)])]);
  assert.throws(() => planConversationActions([s], [d], new Set()), /changed/);
  assert.throws(() => planConversationActions([summary('a', 1)],
    [{...d, id: 'b'}], new Set()), /changed/);
  assert.throws(() => planConversationActions([summary('a', 1, 99)], [d], new Set()), /changed/);
  assert.throws(() => planConversationActions([summary('a', 1)],
    [detail('a', [member(1, 'INBOX', [])])], new Set()), /changed/);
  assert.throws(() => planConversationActions([summary('a', 1)],
    [detail('a', [member(1, 'INBOX', [ref('INBOX', 1), ref('Archive', -1)])])], new Set()), /invalid/);
});

test('deduplicates the same physical copy when represented twice', () => {
  const plan = planConversationActions([summary('a', 1)],
    [detail('a', [member(1, 'INBOX', [ref('INBOX', 1), ref('INBOX', 1)])])], new Set());
  assert.equal(plan.copyCount, 1);
  assert.deepEqual(plan.groups[0].uids, [1]);
});

test('deduplicates copies repeated across different logical members', () => {
  const plan = planConversationActions([summary('a', 2)], [detail('a', [
    member(1, 'INBOX', [ref('INBOX', 1)]),
    member(2, 'INBOX', [ref('INBOX', 1), ref('INBOX', 2)]),
  ])], new Set());
  assert.equal(plan.copyCount, 2);
  assert.deepEqual(plan.groups[0].uids, [1, 2]);
});

test('PostBox wires selectable conversations to guarded bulk actions and a count dialog', async () => {
  const {readFileSync} = await import('node:fs');
  const mailbox = readFileSync(new URL('../app/postbox/(app)/page.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(mailbox, /<input type="checkbox" disabled aria-label=\{"Conversation:/);
  for (const required of [
    'checked={selectedConversationIds.has(conversation.id)}',
    'setSelectedConversations(next);',
    'planConversationActions(snapshots, details, sentFolderNames)',
    'fresh.copyCount !== pending.plan.copyCount',
    'role="alertdialog" aria-modal="true"',
    '{conversation.messageCount} message',
    '{ uid_validity: group.uidValidity }',
  ]) assert.ok(mailbox.includes(required), `Missing conversation UI safety contract: ${required}`);
});
