"""
API key scopes.

An API key used to authenticate *as the user who created it*, inheriting every
permission that user held — including `is_platform_admin`. A key minted by
MateMail staff for a customer workspace was therefore a platform-admin
credential in a header, and a key minted by a workspace owner could do anything
the owner could.

The model here is deliberately small. Scopes describe the resource a key may
change, not individual endpoints; a long list of fine-grained scopes reads as
thorough and in practice gets granted wholesale because nobody can reason about
it. Five values, resource shaped:

    read              every GET/HEAD/OPTIONS in the key's own workspace
    domains:write     domains, their DNS checks, ownership verification
    mailboxes:write   mailboxes and their credentials
    routing:write     aliases and forwarding
    admin             workspace administration — team, keys, billing, backups,
                      queue and quarantine actions, workspace settings

Every key holds `read`. Nothing else is implied by who created it: a key made
by an owner is read-only until a write scope is asked for explicitly.

Two things no scope can ever grant, enforced separately in the middleware:
platform administration, and the internal Mail Engine bridge. Those are not
customer surface, and an API key is a customer credential.
"""
from django.db import models


class APIKeyScope(models.TextChoices):
    READ = "read", "Read"
    DOMAINS_WRITE = "domains:write", "Manage domains"
    MAILBOXES_WRITE = "mailboxes:write", "Manage mailboxes"
    ROUTING_WRITE = "routing:write", "Manage aliases and forwarding"
    ADMIN = "admin", "Workspace administration"


ALL_SCOPES = frozenset(choice.value for choice in APIKeyScope)

#: Every key gets this, and it is the only scope a new key gets by default.
DEFAULT_SCOPES = [APIKeyScope.READ.value]

#: Prefixes an API key may never reach, whatever its scopes.
#:
#: /api/platform/ — internal MateMail staff surface, cross-tenant by design.
#: /api/internal/ — the Mail Engine and webmail bridge, authenticated by
#:                  INTERNAL_API_SECRET and denied at the edge by nginx.
#: /api/auth/     — session and credential management. An API key is not a
#:                  session and must not be able to mint one, change a
#:                  password, or alter a second factor.
FORBIDDEN_PREFIXES = ("/api/platform/", "/api/internal/", "/api/auth/")

#: Write scope required per resource prefix. Order matters only in that the
#: first match wins; the prefixes here do not overlap.
WRITE_SCOPE_BY_PREFIX = (
    ("/api/domains/", APIKeyScope.DOMAINS_WRITE.value),
    ("/api/mailboxes/", APIKeyScope.MAILBOXES_WRITE.value),
    ("/api/aliases/", APIKeyScope.ROUTING_WRITE.value),
    ("/api/forwarding/", APIKeyScope.ROUTING_WRITE.value),
    ("/api/workspaces/", APIKeyScope.ADMIN.value),
    ("/api/teams/", APIKeyScope.ADMIN.value),
    ("/api/billing/", APIKeyScope.ADMIN.value),
    ("/api/backups/", APIKeyScope.ADMIN.value),
    ("/api/queue/", APIKeyScope.ADMIN.value),
    ("/api/quarantine/", APIKeyScope.ADMIN.value),
    ("/api/logs/", APIKeyScope.ADMIN.value),
    ("/api/webmail/", APIKeyScope.ADMIN.value),
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def normalise(scopes) -> list[str]:
    """
    Clean a scope list from input or storage.

    Unknown values are dropped rather than rejected, and `read` is always
    present: a key whose stored scopes are corrupt or empty degrades to
    read-only instead of to unrestricted or to unusable.
    """
    if not isinstance(scopes, (list, tuple, set)):
        scopes = []
    cleaned = {str(s).strip() for s in scopes}
    cleaned = {s for s in cleaned if s in ALL_SCOPES}
    cleaned.add(APIKeyScope.READ.value)
    return sorted(cleaned)


def required_scope(path: str, method: str) -> str | None:
    """
    The scope a request needs, or None when no scope can authorise it.

    None means deny. It is returned for a forbidden prefix and for any
    unmapped write path — a new mutating endpoint is refused to API keys until
    somebody maps it deliberately, rather than being open by omission.
    """
    if path.startswith(FORBIDDEN_PREFIXES):
        return None

    if method.upper() in SAFE_METHODS:
        return APIKeyScope.READ.value

    for prefix, scope in WRITE_SCOPE_BY_PREFIX:
        if path.startswith(prefix):
            return scope

    return None


def permits(scopes, path: str, method: str) -> bool:
    needed = required_scope(path, method)
    if needed is None:
        return False
    return needed in normalise(scopes)
