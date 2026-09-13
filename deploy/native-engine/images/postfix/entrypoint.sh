#!/bin/sh
# Start Postfix in the foreground so Docker owns the process lifecycle.
#
# `postfix start-fg` is the supported way to run Postfix as PID 1's child: it
# performs the same startup checks as `postfix start` and then stays attached,
# so a crash is a container restart rather than a silently dead daemon behind a
# running container.
set -eu

# The spool is a volume, so ownership has to be asserted on every start.
postfix set-permissions 2>/dev/null || true
postfix check

exec postfix start-fg
