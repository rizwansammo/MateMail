import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import test from 'node:test';

const root = process.cwd();
const read = (path) => readFileSync(join(root, path), 'utf8');
const shell = read('app/postbox/(app)/layout.tsx');
const mailbox = read('app/postbox/(app)/page.tsx');
const api = read('lib/postbox-api.ts');
const provider = read('contexts/postbox-context.tsx');
const settings = read('components/postbox/premium-settings.tsx');
const thread = read('components/postbox/conversation-reader.tsx');
const compose = read('components/postbox/compose.tsx');
const visual = read('app/postbox/(app)/postbox-astra-integration.css');
const reader = read('app/postbox/(app)/postbox-astra-reader.css');

test('app rail has real Mail, Contacts and Settings routes', () => {
  assert.match(shell, /className="pb-astra-app-rail"/);
  for (const route of ['/postbox?folder=INBOX', '/postbox/contacts', '/postbox/settings?section=appearance']) {
    assert.ok(shell.includes('href="'+route+'"'), 'Missing route: '+route);
  }
  assert.match(shell, /aria-current=/);
  assert.match(shell, /<PremiumFolderNavigation/);
});

test('real mailbox and identity permission model remains authoritative', () => {
  for (const term of ['authenticatedMailbox', 'availableMailboxes', 'permissions.can_read',
    'permissions.can_manage', 'can_send_as', 'can_send_on_behalf', 'access_type === "delegation"',
    'access_type === "team_box"', 'postbox.switchAccount(item.session_id)', 'switchMailbox(target.id)']) {
    assert.ok(shell.includes(term), 'Required access contract missing: '+term);
  }
  assert.match(shell, /Delegated ·/);
  assert.doesNotMatch(shell, /className="pb-premium-account-contacts"/);
  assert.ok(shell.indexOf('{otherAccounts.length > 0') < shell.indexOf('{(mailbox.kind === "team_box"'));
  assert.match(provider, /authenticatedMailbox/);
});

test('existing live mail and conversation operations are used', () => {
  for (const name of ['postbox.messages(', 'postbox.conversations(', 'postbox.conversationForMessage(',
    'postbox.message(', 'postbox.act(', 'postbox.replyContext(', '<ConversationReader']) {
    assert.ok(mailbox.includes(name), 'Missing live reader/mail operation: '+name);
  }
  assert.match(thread, /onApplyLabel/);
  assert.match(thread, /onReply/);
  assert.match(compose, /postbox.send\(/);
  assert.match(compose, /postbox.saveDraft\(/);
});

test('real sessions, realtime and API action contracts stay available', () => {
  assert.match(shell, /new EventSource\("\/api\/postbox\/events\/"\)/);
  assert.match(shell, /installPostBoxCrossTabSync/);
  assert.match(shell, /POSTBOX_MAILBOX_EVENT/);
  for (const field of ['switchAccount:', 'switchMailbox:', 'send:', 'saveDraft:', 'preferences:',
    'account:', 'identities:', 'forwarding:', 'sessions:']) {
    assert.ok(api.includes(field), 'API contract missing '+field);
  }
});

test('all eleven production settings remain in the reviewed order', () => {
  const begin = settings.indexOf('const AREAS = [');
  assert.ok(begin>=0);
  const nav=settings.slice(begin,settings.indexOf('] as const;', begin));
  const expected=['appearance','mail','account','identities','signatures','rules','vacation',
    'forwarding','notifications','security','clients'];
  const actual=[...nav.matchAll(/\["([a-z]+)",\s*"[^"]+",/g)].map(m=>m[1]);
  assert.deepEqual(actual,expected);
  assert.match(settings,/postbox\.me\(\)\)\.mail_client/);
});

test('approved reader density and sharp thread corners are scoped', () => {
  for (const value of ['.pb-astra-postbox .pb-thread-card{border-radius:10px}',
    '.pb-astra-postbox.pb-density-compact .pb-thread-card{border-radius:6px}',
    '.pb-astra-postbox.pb-density-extra-compact .pb-thread-card,']) {
    assert.ok(reader.includes(value),'Missing approved density rule: '+value);
  }
  assert.match(visual, /pb-density-extra-compact \.pb-premium-message-row\{min-height:41px/);
  assert.match(visual, /\.pb-astra-postbox \.pb-settings-content/);
  assert.doesNotMatch(shell, /import .*['"]\.\/model['"]/);
  assert.doesNotMatch(shell, /seed\(/);
});
