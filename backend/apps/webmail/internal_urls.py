"""
Internal-only webmail routes — none.

`validate-token/` existed to let a webmail application exchange an SSO token
for a mailbox address. The token it validated could be minted for somebody
else's mailbox, so both halves were removed together in P11; keeping the
validator would have left the second half of a mechanism whose first half was
the flaw. See apps/webmail/views.py and DEC-049.
"""
urlpatterns: list = []
