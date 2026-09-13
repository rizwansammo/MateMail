# MateMail Native Engine — Rspamd global options.
#
# WHAT: DNS behaviour and timeout posture.
# WHY:  DNSSEC on, resolving through the engine's own validating Unbound.
#       soft_reject_on_timeout makes a slow scanner DEFER rather than let mail
#       through unscanned — the fail-closed rule from NE0.11 and NE0.17.
# PHASE: NE1.
dns {
    enable_dnssec = true;
    nameserver = "172.27.0.53";
}
soft_reject_on_timeout = true;
task_timeout = 30s;
