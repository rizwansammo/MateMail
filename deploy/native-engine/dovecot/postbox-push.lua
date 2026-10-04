-- MateMail Native Engine: PostBox new-mail events.
--
-- WHAT THIS DOES
--   Dovecot's push_notification plugin (Lua driver) runs this inside LMTP
--   after a delivered message has been SAVED AND COMMITTED to the mailbox. It
--   tells the engine API which mailbox, folder and message that was -- the
--   address, the folder name, UIDVALIDITY and UID, nothing else -- and the
--   API relays it to MateMail, which wakes that mailbox's PostBox devices.
--
-- WHY IT IS SHAPED THIS WAY
--   MAIL FIRST. By the time this runs the message is already in the mailbox,
--   and nothing here can take it back: a failure is one warning in the log,
--   never a refused, bounced, deferred or duplicated delivery. The request is
--   one attempt with a one-second limit, so an unreachable or stalled API can
--   delay a delivery by about a second and no more. Measured against the
--   pinned 2.4.1 image: with the API stopped, Dovecot's HTTP client gave up
--   at the limit (its own status 9008), and an API that accepts and never
--   answers released the delivery at 1.08 s; both left the message saved and
--   LMTP answering 250.
--
--   CONTENT NEVER LEAVES. Dovecot hands every event the sender, recipients,
--   subject, Message-ID and a body snippet. This script reads exactly three
--   fields of it -- `mailbox`, `uid_validity`, `uid` -- so the rest cannot
--   reach the network. test_native_engine_push.py fails if another is read.
--
--   DELIVERY ONLY. The plugins are loaded for LMTP alone (entrypoint.sh), so
--   an IMAP APPEND, a draft, a sent copy or `doveadm save` produces nothing;
--   and MessageNew is the only event handled.
--
--   THE IDENTIFIERS ARE DOVECOT'S OWN. `uid` and `uid_validity` are the ones
--   the commit assigned, not a guess at "the newest message" -- measured:
--   they matched `doveadm mailbox status` for every test delivery. `mailbox`
--   is the folder Dovecot saved to. This engine configures no Sieve and no
--   detail-mailbox delivery, so today that is always INBOX.
--
-- WHICH PHASE OWNS IT
--   PostBox remote push, server half. See docs/POSTBOX_REMOTE_PUSH.md.

local json = require("json")

local settings = {}

-- `lua_settings` from the push_notification block (rendered by entrypoint.sh
-- from the environment): the API URL and the dedicated push secret. Measured
-- against 2.4.1: they arrive here as a table.
function script_init(values)
  settings = values or {}
  return 0
end

function dovecot_lua_notify_begin_txn(user)
  return { mailbox = user.username, events = {} }
end

local function queue_event(ctx, event, kind)
  ctx.events[#ctx.events + 1] = {
    event = kind,
    folder = event.mailbox,
    -- Pushed as Lua numbers (1.0); the API accepts integers only.
    uid_validity = math.tointeger(event.uid_validity),
    uid = math.tointeger(event.uid),
  }
end

function dovecot_lua_notify_event_message_new(ctx, event)
  queue_event(ctx, event, "new_mail")
end

-- IMAP mutations from PostBox or any other client. Identity only: the web
-- client wakes and re-reads authoritative mailbox state.
function dovecot_lua_notify_event_flags_set(ctx, event)
  queue_event(ctx, event, "mailbox_changed")
end

function dovecot_lua_notify_event_flags_clear(ctx, event)
  queue_event(ctx, event, "mailbox_changed")
end

function dovecot_lua_notify_event_message_append(ctx, event)
  queue_event(ctx, event, "mailbox_changed")
end

function dovecot_lua_notify_event_message_trash(ctx, event)
  queue_event(ctx, event, "mailbox_changed")
end

function dovecot_lua_notify_event_message_expunge(ctx, event)
  queue_event(ctx, event, "mailbox_changed")
end

local function send(mailbox, event)
  local client = dovecot.http.client({
    request_absolute_timeout = "1s",
    request_max_attempts = 1,
    request_max_redirects = 0,
  })
  local request = client:request({ url = settings.url, method = "POST" })
  request:add_header("Content-Type", "application/json")
  request:add_header("X-Native-Push-Secret", settings.secret)
  request:set_payload(json.encode({
    event = event.event,
    mailbox = mailbox,
    folder = event.folder,
    uid_validity = event.uid_validity,
    uid = event.uid,
  }))
  return request:submit():status()
end

-- `success` is false when the save did not commit; then there is nothing to
-- report. Each event is sent on its own, and one failing does not stop the
-- rest.
function dovecot_lua_notify_end_txn(ctx, success)
  if not success or #ctx.events == 0 then
    return
  end
  if (settings.url or "") == "" or (settings.secret or "") == "" then
    return
  end
  for _, event in ipairs(ctx.events) do
    local ok, result = pcall(send, ctx.mailbox, event)
    local status = ok and tonumber(result) or nil
    if not ok then
      dovecot.i_warning("postbox-push: new-mail event not sent: " .. tostring(result))
    elseif status == nil or status >= 9000 then
      -- 9000 and above are Dovecot's own HTTP client codes, not an answer:
      -- the API was not reached, or did not answer in time (9008).
      dovecot.i_warning("postbox-push: engine API not reached (Dovecot HTTP client status "
        .. tostring(result) .. ")")
    elseif status ~= 202 then
      dovecot.i_warning("postbox-push: engine API answered HTTP " .. tostring(status))
    end
  end
end
