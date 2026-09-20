"""
Tenant-facing webmail routes — none.

The SSO bridge that lived here was removed in P11. It allowed any user with
tenant access to mint a login token for any mailbox in the organization, which
is the mailbox-impersonation path the PostBox privacy boundary forbids. See
apps/webmail/views.py and DEC-049.

PostBox authenticates the mailbox user directly: /api/postbox/auth/login/.
"""
urlpatterns: list = []
