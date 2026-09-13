"""
Input validation for the Native Engine provisioning API.

EVERY endpoint validates before it touches the database. Not because the
database lacks constraints — it has them — but because a constraint violation
produces an error shaped like a database, and the caller needs an error shaped
like the thing it asked for.

FAIL CLOSED, AND FAIL NARROW
    Unknown JSON fields are rejected rather than ignored. Ignoring them is how a
    typo in `active` silently provisions a mailbox in the wrong state, and how a
    field a future version adds gets quietly dropped by an older engine that
    reports success.
"""
from __future__ import annotations

import re

#: Same shape MateMail's own DomainCreateSerializer enforces. Deliberately
#: identical: an engine that accepted names the product rejects would let state
#: exist that MateMail can never address, and one that is stricter would refuse
#: domains a customer has already paid for.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\.[a-z]{2,63}$"
)

#: RFC 5321 caps the local part at 64 octets and the whole path at 256; the
#: address itself is conventionally held to 254. Kept deliberately permissive
#: about WHICH characters, because the engine is not the place to relitigate
#: what a valid local part is — it is the place to refuse the shapes that would
#: corrupt a lookup, a header or a Postfix map.
_LOCAL_PART_RE = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")

#: A DKIM selector becomes a DNS label, so it is bound by DNS rules, not ours.
_SELECTOR_RE = re.compile(r"^(?!-)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

#: PostgreSQL `integer`. A quota above this would be rejected by the column, so
#: it is rejected here where the message can say why.
_INT32_MAX = 2_147_483_647


class ValidationError(ValueError):
    """A caller error. Carries a message safe to return in an API response."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.message = message
        self.field = field


def domain_name(value, field: str = "domain") -> str:
    """
    Normalise and validate a domain name.

    IDNA IS A DELIBERATE REFUSAL, NOT AN OVERSIGHT.

    MateMail's own validator accepts ASCII A-labels only, so a U-label
    ("münchen.example") cannot reach the engine through the product path. That
    leaves two options for a non-ASCII name arriving here anyway: transcode it,
    or refuse it.

    Transcoding with the standard library means IDNA 2003, which disagrees with
    IDNA 2008/UTS-46 on characters real registries use — ß and ς among them. The
    engine would then store one label while MateMail published DNS for another,
    and DKIM would verify against a name nobody queries. That is the exact class
    of silent divergence this project keeps paying for.

    So non-ASCII is refused with a message that says so. Supporting it is a real
    decision requiring the `idna` package and a matching change in MateMail's
    validator, made together, not inferred here.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field)
    name = value.strip().rstrip(".").lower()
    if not name:
        raise ValidationError(f"{field} must not be empty", field)
    if not name.isascii():
        raise ValidationError(
            f"{field} must be an ASCII A-label; internationalised names are not "
            f"supported by this engine (convert to punycode before provisioning)",
            field,
        )
    if not _HOSTNAME_RE.match(name):
        raise ValidationError(f"{field} is not a valid domain name: {name!r}", field)
    return name


def email_address(value, field: str = "address") -> str:
    """Normalise and validate an address, returning it lowercased."""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field)
    address = value.strip().lower()
    if not address:
        raise ValidationError(f"{field} must not be empty", field)
    if len(address) > 254:
        raise ValidationError(f"{field} is longer than 254 characters", field)
    local, sep, domain = address.rpartition("@")
    if not sep or not local:
        raise ValidationError(f"{field} must be local@domain, got {address!r}", field)
    if len(local) > 64:
        raise ValidationError(f"{field} local part exceeds 64 characters", field)
    if not address.isascii():
        raise ValidationError(
            f"{field} must be ASCII; SMTPUTF8 addresses are not supported by this engine",
            field,
        )
    if not _LOCAL_PART_RE.match(local):
        raise ValidationError(f"{field} has an invalid local part: {local!r}", field)
    domain_name(domain, field=f"{field} domain")
    return address


def destination_address(value, field: str = "destination") -> str:
    """
    A delivery target. Same rules as any address — it is one.

    Kept as a distinct function so the error names the field the caller used,
    and so a future relaxation (say, allowing a bare local part) has one place
    to happen rather than being scattered through the handlers.
    """
    return email_address(value, field=field)


def selector(value, field: str = "selector") -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field)
    text = value.strip().lower()
    if not text:
        raise ValidationError(f"{field} must not be empty", field)
    if not _SELECTOR_RE.match(text):
        raise ValidationError(
            f"{field} must be a single DNS label (letters, digits, hyphens), got {text!r}",
            field,
        )
    return text


def quota_mb(value, field: str = "quota_mb") -> int:
    """
    A storage quota in MEGABYTES.

    The unit is the adapter contract's (`MailboxSpec.quota_mb`) and is not
    reinterpreted here. Zero is rejected rather than being given a meaning:
    neither the DTO nor the engine assigns "unlimited" to it, and inventing that
    now would make a typo into an unbounded mailbox.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be a whole number of megabytes", field)
    if value <= 0:
        raise ValidationError(
            f"{field} must be greater than zero (megabytes), got {value}", field
        )
    if value > _INT32_MAX:
        raise ValidationError(f"{field} is larger than the engine can store", field)
    return value


def boolean(value, field: str) -> bool:
    """Strictly a JSON boolean. `"false"` is a string and a caller error."""
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be true or false", field)
    return value


def password(value, field: str = "password") -> str:
    """
    A plaintext password on its way to being hashed, and nowhere else.

    Never echoed in an error. The messages below describe the PROBLEM, never the
    value — an error carrying "'hunter2' is too short" would put the credential
    in a log the moment anyone logged the response.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string", field)
    if not value:
        raise ValidationError(f"{field} must not be empty", field)
    if len(value) > 1024:
        raise ValidationError(f"{field} is longer than 1024 characters", field)
    return value


def destinations(value, field: str = "destinations") -> tuple[str, ...]:
    """
    A delivery set: a list of addresses, order preserved, duplicates collapsed.

    Order is preserved because it is the caller's, and the adapter contract says
    the destination set is applied verbatim.
    """
    if not isinstance(value, list):
        raise ValidationError(f"{field} must be a list of addresses", field)
    seen, out = set(), []
    for index, item in enumerate(value):
        address = destination_address(item, field=f"{field}[{index}]")
        if address not in seen:
            seen.add(address)
            out.append(address)
    return tuple(out)


def payload(body: dict, *, allowed: set[str], required: set[str] = frozenset()) -> dict:
    """
    Reject unknown and missing fields before anything else looks at the body.

    Unknown fields are an error rather than something to ignore: silently
    dropping one turns a caller's typo into a successful request that did not do
    what it said.
    """
    if not isinstance(body, dict):
        raise ValidationError("request body must be a JSON object")
    unknown = set(body) - allowed
    if unknown:
        raise ValidationError(f"unknown field(s): {', '.join(sorted(unknown))}")
    missing = required - set(body)
    if missing:
        raise ValidationError(f"missing required field(s): {', '.join(sorted(missing))}")
    return body
