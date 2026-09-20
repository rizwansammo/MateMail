"""
Webmail SSO bridge — REMOVED.

WHAT WAS HERE, AND WHY IT IS GONE
    `WebmailSSOView` let any authenticated user with `HasTenantAccess` mint a
    60-second token for ANY mailbox in their organization:

        mailbox = Mailbox.objects.for_tenant(request.tenant).filter(pk=...)

    The queryset was scoped to the tenant, which is the wrong boundary. It
    never checked that the caller *owns* the mailbox, so an organization
    administrator could mint a login token for an employee's mailbox. The
    Workspace UI shipped a button that did exactly that, on the mailbox detail
    page, labelled "Open webmail".

    It was inert only because nothing could complete the login — MateMail
    stores no mailbox password, so the token was a key to a door that had not
    been built. P11 builds that door. Finishing this mechanism would have
    turned a dormant design flaw into a working mailbox-impersonation path on
    the day PostBox shipped.

    The privacy boundary MateMail commits to is that an organization
    administrator does not silently read an employee's mail, and neither does
    a platform administrator. Provisioning a mailbox, suspending it, resetting
    its password and reading its contents are four different powers; the first
    three belong to administrators and the fourth belongs to the mailbox user.

THE REPLACEMENT
    `apps.postbox` authenticates the mailbox user directly against Dovecot with
    the password they type, and binds the session to exactly one mailbox. There
    is no request field naming a mailbox and no token minted on anyone else's
    behalf — see DEC-049.

    This module is left in place, empty, rather than deleted outright: the URLs
    are gone, and a future reader looking for the "webmail SSO" they remember
    finds this explanation instead of an absence.
"""
