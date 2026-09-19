#!/usr/bin/env bash
# Shared restore guards.
#
# This lives in its own file because `matemail-restore-mailbox.sh` is the one
# script in P6 that can destroy customer mail, and the rule it enforces —
# restoring old mail must never silently overwrite a mailbox somebody is
# currently using — deserves to be tested directly rather than only through a
# script that needs Docker, restic and a populated repository to run at all.

# mailbox_target_state <maildir>
#
# Classifies a restore target. Prints exactly one word:
#
#   absent   nothing is there. Restoring in place is safe.
#   empty    the directory exists but holds no messages. A provisioned but
#            never-used mailbox looks like this. Restoring in place is safe.
#   active   it holds at least one message. Restoring in place would overwrite
#            mail that exists only here. REFUSE unless the operator says
#            otherwise, in writing, on the command line.
#
# "Holds a message" means a regular file under cur/ or new/ anywhere in the
# tree. tmp/ is deliberately not counted: Maildir uses it for deliveries that
# have not been committed yet, so a file there is not yet mail, and treating
# leftover tmp/ junk as "active" would block legitimate restores into mailboxes
# that are in fact empty.
mailbox_target_state() {
    local target="$1"

    if [ ! -e "$target" ]; then
        echo absent
        return 0
    fi

    # -print -quit stops at the first hit: an active mailbox is classified
    # without walking a million messages.
    local hit
    hit=$(find "$target" -type d \( -name cur -o -name new \) \
              -exec find {} -maxdepth 1 -type f -print -quit \; 2>/dev/null \
          | head -n 1)

    if [ -n "$hit" ]; then
        echo active
    else
        echo empty
    fi
}
