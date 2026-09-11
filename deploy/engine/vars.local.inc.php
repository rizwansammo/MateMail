<?php
/*
 * MateMail — local mailcow overrides.
 *
 * Why this file exists:
 *   DEC-007r requires that DKIM private keys never leave the Mail Engine. The
 *   engine's DKIM read (functions.dkim.inc.php) returns a `privkey` field that
 *   is empty ONLY while $SHOW_DKIM_PRIV_KEYS is false. That is the shipped
 *   default in vars.inc.php, but vars.inc.php is replaced on every mailcow
 *   update — so relying on the default means relying on an upstream choice we
 *   do not control.
 *
 *   Pinning it here makes the guarantee explicit, auditable, and persistent:
 *   vars.local.inc.php is the upstream-supported override file and is not
 *   touched by updates.
 *
 *   MateMail's adapter also strips private DKIM fields unconditionally, but
 *   that is defence in depth. This line is the primary control: it stops the
 *   key crossing the engine boundary in the first place.
 *
 *   DO NOT set this to true. Doing so is an engine security misconfiguration
 *   and blocks activation of the real MateMail adapter.
 */
$SHOW_DKIM_PRIV_KEYS = false;
