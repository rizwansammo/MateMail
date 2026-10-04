"""
Compiling PostBox rules into Sieve, and installing them over ManageSieve.

WHY COMPILE RATHER THAN ACCEPT SIEVE
    Sieve is a programming language that runs as the mail server, and it has
    `redirect` — which forwards a copy of somebody's mail anywhere. A browser
    that could post raw Sieve would be a browser that could exfiltrate a
    mailbox with one API call.

    So the browser posts a condition and an action from a fixed vocabulary,
    and this module renders the script. The generated text is the only Sieve
    that ever reaches Dovecot, every value in it is quoted by `_quote`, and
    the rule vocabulary contains no forwarding action at all.

WHY IT IS ONE SCRIPT
    Sieve evaluates a single active script top to bottom. Rules and the
    vacation responder therefore have to be rendered together, in order, with
    `stop` where the user asked for it — generating two scripts and hoping
    Dovecot merges them is not a thing Sieve does.

HOW IT IS INSTALLED
    ManageSieve (RFC 5804) on the engine network, authenticated with the same
    master identity PostBox reads mail with. Not by writing a file: Django has
    no access to the mail store's filesystem and should not have, and a script
    written directly would not be compiled or activated.
"""
from __future__ import annotations

import logging
import re
import socket
import ssl
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

#: The script PostBox owns. A mailbox may in principle have others; PostBox
#: only ever writes and activates this one, so a script installed by some other
#: tool is left alone rather than silently replaced.
SCRIPT_NAME = "matemail-postbox"


class SieveError(Exception):
    def __init__(self, customer_message: str, log_message: str = ""):
        super().__init__(customer_message)
        self.customer_message = customer_message
        self.log_message = log_message or customer_message


@dataclass
class CompiledScript:
    body: str
    requires: list[str]


def _quote(value: str) -> str:
    """
    A Sieve quoted-string.

    Backslash first — reversing the order would escape the backslashes that
    the quote-escaping just added. Control characters are removed rather than
    escaped: a newline inside a string would end the statement, and no
    legitimate rule contains one.
    """
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", value or "")
    return '"' + cleaned.replace("\\", "\\\\").replace('"', '\\"') + '"'


#: Text comparisons. Negative matches are compiled as Sieve's `not`
#: wrapper, keeping raw code generation inside this module's fixed vocabulary.
_MATCH = {
    "contains": (":contains", False),
    "is": (":is", False),
    "not_contains": (":contains", True),
    "not_is": (":is", True),
}


def _rule_conditions(rule) -> list[dict]:
    raw = getattr(rule, "conditions", None)
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    return [{
        "field": getattr(rule, "field", ""),
        "match": getattr(rule, "match", ""),
        "value": getattr(rule, "value", ""),
    }]


def _rule_actions(rule) -> list[dict]:
    raw = getattr(rule, "actions", None)
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    item = {"action": getattr(rule, "action", "")}
    folder = getattr(rule, "action_folder", "")
    if folder:
        item["folder"] = folder
    return [item]


def _text_test(kind: str, header_list: str, match: str, value: str) -> str | None:
    spec = _MATCH.get(match)
    if spec is None:
        return None
    comparator, negate = spec
    test = f"{kind} {comparator} [{header_list}] {_quote(value)}"
    return f"not {test}" if negate else test


def _condition_test(condition: dict) -> tuple[str | None, set[str]]:
    field = condition.get("field")
    match = condition.get("match")
    value = str(condition.get("value", ""))
    requires: set[str] = set()

    if field == "from":
        return _text_test("address", _quote("From"), match, value), requires
    if field == "to":
        headers = ", ".join((_quote("To"), _quote("Cc")))
        return _text_test("address", headers, match, value), requires
    if field == "sender_domain":
        spec = _MATCH.get(match)
        if spec is None:
            return None, requires
        comparator, negate = spec
        test = f"address :domain {comparator} [{_quote('From')}] {_quote(value)}"
        return (f"not {test}" if negate else test), requires
    if field == "subject":
        return _text_test("header", _quote("Subject"), match, value), requires
    if field == "mailing_list":
        return _text_test("header", _quote("List-ID"), match, value), requires
    if field == "body":
        spec = _MATCH.get(match)
        if spec is None:
            return None, requires
        requires.add("body")
        comparator, negate = spec
        test = f"body {comparator} {_quote(value)}"
        return (f"not {test}" if negate else test), requires
    if field == "message_size":
        if match not in {"over", "under"}:
            return None, requires
        try:
            size_kb = int(value)
        except (TypeError, ValueError):
            return None, requires
        return f"size :{match} {size_kb}K", requires
    if field == "has_attachment":
        requires.add("mime")
        test = (
            "anyof("
            "header :mime :anychild :contains [\"Content-Disposition\"] \"attachment\", "
            "header :mime :anychild :contains [\"Content-Disposition\"] \"filename=\", "
            "header :mime :anychild :contains [\"Content-Type\"] \"name=\""
            ")"
        )
        return (f"not {test}" if value.lower() == "no" else test), requires
    if field == "attachment_name":
        spec = _MATCH.get(match)
        if spec is None:
            return None, requires
        requires.add("mime")
        comparator, negate = spec
        headers = ", ".join((_quote("Content-Disposition"), _quote("Content-Type")))
        test = f"header :mime :anychild {comparator} [{headers}] {_quote(value)}"
        return (f"not {test}" if negate else test), requires
    return None, requires


def _folder_for_action(action: dict) -> str:
    kind = action.get("action")
    if kind in {"move", "copy"}:
        return str(action.get("folder", "")).strip()
    if kind == "archive":
        return "Archive"
    if kind == "delete":
        return "Trash"
    return ""


def compile_rules(rules, vacation=None, *, valid_folders: set[str] | None = None) -> CompiledScript:
    """
    Render ordered Rules v2 plus the vacation responder.

    Each rule is a bounded boolean expression (ALL/ANY, max enforced by the
    serializer) and a bounded action list. The browser never sends Sieve.
    Folder actions are all-or-nothing: if any destination vanished, the whole
    rule is skipped rather than partially doing something the user did not ask.
    """
    requires: set[str] = set()
    lines: list[str] = [
        "# Generated by MateMail PostBox. Do not edit by hand:",
        "# this script is replaced whenever the mailbox's rules change.",
        "",
    ]

    for rule in rules:
        if not rule.enabled:
            continue

        conditions = _rule_conditions(rule)
        actions = _rule_actions(rule)
        tests: list[str] = []
        condition_requires: set[str] = set()
        invalid = False
        for condition in conditions:
            test, needed = _condition_test(condition)
            condition_requires.update(needed)
            if not test:
                invalid = True
                break
            tests.append(test)
        if invalid or not tests or not actions:
            logger.warning("PostBox: skipping malformed rule %s", getattr(rule, "pk", None))
            continue

        for action in actions:
            folder = _folder_for_action(action)
            if folder and valid_folders is not None and folder not in valid_folders:
                logger.warning(
                    "PostBox: rule %s targets missing folder %r — skipped",
                    getattr(rule, "pk", None), folder,
                )
                invalid = True
                break
        if invalid:
            continue

        requires.update(condition_requires)
        mode = getattr(rule, "condition_mode", "all")
        if len(tests) == 1:
            expression = tests[0]
        else:
            operator = "anyof" if mode == "any" else "allof"
            expression = f"{operator}({', '.join(tests)})"

        lines.append(f"# {rule.name}")
        lines.append(f"if {expression}")
        lines.append("{")

        for action in actions:
            kind = action.get("action")
            if kind == "move":
                requires.add("fileinto")
                lines.append(f"    fileinto {_quote(_folder_for_action(action))};")
            elif kind == "copy":
                requires.update({"fileinto", "copy"})
                lines.append(f"    fileinto :copy {_quote(_folder_for_action(action))};")
            elif kind == "archive":
                requires.add("fileinto")
                lines.append('    fileinto "Archive";')
            elif kind == "star":
                requires.add("imap4flags")
                lines.append('    addflag "\\\\Flagged";')
            elif kind == "mark_read":
                requires.add("imap4flags")
                lines.append('    addflag "\\\\Seen";')
            elif kind == "mark_unread":
                requires.add("imap4flags")
                lines.append('    removeflag "\\\\Seen";')
            elif kind == "delete":
                requires.add("fileinto")
                lines.append('    fileinto "Trash";')
            else:
                logger.warning(
                    "PostBox: rule %s has unknown action %r — skipped at compile",
                    getattr(rule, "pk", None), kind,
                )
                continue

        if rule.stop_processing:
            lines.append("    stop;")
        lines.append("}")
        lines.append("")

    if vacation is not None and vacation.is_active_now and vacation.message.strip():
        requires.add("vacation")
        days = max(1, min(int(vacation.repeat_days or 7), 60))
        lines.append("# Automatic reply")
        lines.append("vacation")
        lines.append(f"    :days {days}")
        if vacation.subject.strip():
            lines.append(f"    :subject {_quote(vacation.subject.strip())}")
        lines.append(f"    {_quote(vacation.message.strip())};")
        lines.append("")

    header = ""
    if requires:
        header = "require [" + ", ".join(_quote(r) for r in sorted(requires)) + "];\n\n"

    return CompiledScript(body=header + "\n".join(lines), requires=sorted(requires))


# ── ManageSieve ─────────────────────────────────────────────────────────────

class ManageSieveClient:
    """
    A minimal ManageSieve client: PUTSCRIPT, SETACTIVE, DELETESCRIPT.

    Deliberately small. The protocol is line-oriented with literals, and the
    three commands PostBox needs are simple; a general-purpose client would be
    more code to audit for something that writes executable rules to the mail
    server.
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._buffer = b""

    # ── framing ─────────────────────────────────────────────────────────────

    def _read_line(self) -> bytes:
        while b"\r\n" not in self._buffer:
            chunk = self._sock.recv(8192)
            if not chunk:
                raise SieveError(
                    "Your filters could not be saved.", "ManageSieve closed the connection"
                )
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\r\n")
        return line

    def _read_response(self) -> tuple[str, str]:
        """Read until OK / NO / BYE. Returns (status, text)."""
        while True:
            line = self._read_line()
            upper = line.upper()
            if upper.startswith(b"OK"):
                return "OK", line.decode("utf-8", "replace")
            if upper.startswith(b"NO") or upper.startswith(b"BYE"):
                return "NO", line.decode("utf-8", "replace")
            # Capability lines and literals are not interesting to us.

    def _send(self, data: bytes) -> None:
        self._sock.sendall(data)

    # ── commands ────────────────────────────────────────────────────────────

    def authenticate(self, authorize_as: str, master_user: str, password: str) -> None:
        """
        SASL PLAIN, with the authorisation identity set to the mailbox.

        `authzid\\0authcid\\0password` — "act as this mailbox, authenticated as
        the PostBox service identity". The same master mechanism used for IMAP,
        so there is one privileged credential rather than two.
        """
        import base64

        payload = f"{authorize_as}\0{master_user}\0{password}".encode()
        encoded = base64.b64encode(payload).decode()
        self._send(f'AUTHENTICATE "PLAIN" "{encoded}"\r\n'.encode())
        status, text = self._read_response()
        if status != "OK":
            raise SieveError(
                "Your filters could not be saved.", f"ManageSieve AUTHENTICATE -> {text}"
            )

    def put_script(self, name: str, body: str) -> None:
        encoded = body.encode("utf-8")
        self._send(f'PUTSCRIPT "{name}" {{{len(encoded)}+}}\r\n'.encode())
        self._send(encoded + b"\r\n")
        status, text = self._read_response()
        if status != "OK":
            # A compile error lands here. The customer message stays generic —
            # Sieve diagnostics are about generated code they never wrote.
            raise SieveError(
                "Your filters could not be saved.", f"ManageSieve PUTSCRIPT -> {text}"
            )

    def set_active(self, name: str) -> None:
        self._send(f'SETACTIVE "{name}"\r\n'.encode())
        status, text = self._read_response()
        if status != "OK":
            raise SieveError(
                "Your filters were saved but could not be activated.",
                f"ManageSieve SETACTIVE -> {text}",
            )

    def logout(self) -> None:
        try:
            self._send(b"LOGOUT\r\n")
        except OSError:
            pass


def install_script(address: str, body: str) -> None:
    """
    Compile-and-activate this mailbox's script. Replaces any previous version.

    An empty script is still installed, and then deactivated: a mailbox whose
    last rule was deleted must stop filtering, and leaving the old script
    active would keep applying rules the person can no longer see.
    """
    host = getattr(settings, "POSTBOX_SIEVE_HOST", "")
    port = int(getattr(settings, "POSTBOX_SIEVE_PORT", 4190))
    master_user = getattr(settings, "POSTBOX_MASTER_USER", "postbox")
    master_password = getattr(settings, "POSTBOX_MASTER_PASSWORD", "")

    if not host or not master_password:
        raise SieveError(
            "Filters are not available. Please contact your administrator.",
            "POSTBOX_SIEVE_HOST or POSTBOX_MASTER_PASSWORD is unset",
        )

    try:
        sock = socket.create_connection((host, port), timeout=15)
    except OSError as exc:
        raise SieveError(
            "Filters are temporarily unavailable. Please try again shortly.",
            f"ManageSieve connect {host}:{port} failed: {exc!r}",
        ) from exc

    client = ManageSieveClient(sock)
    try:
        # Greeting may carry multiple capabilities; await terminal OK
        # before issuing STARTTLS, or TLS gets confused by plaintext bytes.
        status, greeting = client._read_response()
        if status != "OK":
            raise SieveError("Filters are temporarily unavailable.",
                             f"ManageSieve greeting -> {greeting}")

        use_tls = getattr(settings, "POSTBOX_SIEVE_STARTTLS", False)
        if not use_tls and not getattr(settings, "DEBUG", False):
            raise SieveError(
                "Filters require a secure server connection.",
                "Refusing plaintext ManageSieve authentication in production",
            )
        if use_tls:
            client._send(b"STARTTLS\r\n")
            status, result = client._read_response()
            if status != "OK":
                raise SieveError(
                    "Your filters could not be saved securely.",
                    f"ManageSieve refused STARTTLS -> {result}",
                )
            tls_hostname = getattr(settings, "POSTBOX_SIEVE_TLS_SERVER_NAME", "") or host
            sock = ssl.create_default_context().wrap_socket(
                sock, server_hostname=tls_hostname,
            )
            client = ManageSieveClient(sock)
            status, greeting = client._read_response()
            if status != "OK":
                raise SieveError("Filters are temporarily unavailable.",
                                 f"ManageSieve TLS greeting -> {greeting}")

        client.authenticate(address, master_user, master_password)
        client.put_script(SCRIPT_NAME, body)

        if body.strip():
            client.set_active(SCRIPT_NAME)
        else:
            # Deactivate rather than delete, so the empty script is visible
            # evidence that PostBox owns this slot.
            client._send(b'SETACTIVE ""\r\n')
            status, result = client._read_response()
            if status != "OK":
                raise SieveError(
                    "Your filters could not be disabled.",
                    f"ManageSieve deactivate -> {result}",
                )
    except (ssl.SSLError, OSError) as exc:
        raise SieveError(
            "Filters could not establish a secure connection to the mail server.",
            f"ManageSieve TLS/session failure: {exc!r}",
        ) from exc
    finally:
        client.logout()
        try:
            sock.close()
        except OSError:
            pass


def sync_mailbox_rules(mailbox, *, valid_folders: set[str] | None = None) -> None:
    """
    Render and install everything for one mailbox.

    Called after any rule or vacation change. Failures propagate: a filter the
    person believes is active but which was never installed is worse than an
    error message.
    """
    from .models import MailRule, VacationResponder

    rules = list(MailRule.objects.for_mailbox(mailbox).order_by("position", "created_at"))
    vacation = VacationResponder.objects.filter(mailbox=mailbox).first()

    script = compile_rules(rules, vacation, valid_folders=valid_folders)
    install_script(mailbox.email, script.body)
    logger.info(
        "PostBox: installed %d rule(s) for mailbox %s (vacation=%s)",
        len(rules), mailbox.pk, bool(vacation and vacation.is_active_now),
    )
