#!/usr/bin/env bash
# Ship Native mail logs to a file fail2ban can read.
#
# WHY THIS EXISTS
#   Native Postfix and Dovecot log to stdout under Docker's json-file driver,
#   which is right for P7 (the collector reads `docker logs`) and useless to
#   fail2ban, which watches files. The alternatives were worse: pointing
#   fail2ban at /var/lib/docker/containers/<id>/<id>-json.log breaks every time
#   a container is recreated, and switching those two services to the journald
#   driver would change the logging model the monitoring depends on.
#
#   So one small root service re-emits both streams into one file. It follows
#   with `--tail 0`, so it never replays history and cannot ban somebody for a
#   failure from last week, and it reattaches when a container is recreated.
set -uo pipefail

LOG="${MAIL_LOG:-/var/log/matemail/mail.log}"
mkdir -p "$(dirname "$LOG")"
touch "$LOG"
chmod 640 "$LOG"

follow() {
    local container="$1"
    while true; do
        # Both streams: Postfix writes to stdout, Dovecot to stderr.
        docker logs -f --tail 0 "$container" >> "$LOG" 2>&1 || true
        # The container is being recreated, or Docker is restarting. Wait and
        # reattach rather than exiting, so a deploy does not silently end
        # abuse protection until somebody notices.
        sleep 3
    done
}

for container in "$@"; do
    follow "$container" &
done
wait
