"""
The abuse limits, in one place.

These are the numbers documented in `docs/SECURITY.md` §Rate Limiting. Keeping
them here rather than inline means the documentation, the views and the tests
all read the same values, so a limit cannot be quietly relaxed in one place
only.

Each entry is (bucket name, limit, window in seconds).
"""
from collections import namedtuple

Limit = namedtuple("Limit", "bucket limit window")

MINUTE = 60
QUARTER_HOUR = 15 * MINUTE
HOUR = 60 * MINUTE

#: Failed password attempts from one IP address. Counted on failure and reset
#: on success, so a person who mistypes twice and then succeeds is not carrying
#: those failures, and one NAT gateway cannot lock out an office.
LOGIN_PER_IP = Limit("login:ip", 5, QUARTER_HOUR)

#: Failed password attempts against one account, from anywhere.
LOGIN_PER_ACCOUNT = Limit("login:account", 5, QUARTER_HOUR)

#: New workspaces via signup, per IP.
SIGNUP_PER_IP = Limit("signup:ip", 3, HOUR)

#: Password-reset requests per normalised email address. Counted for every
#: request regardless of whether the account exists, so the limit itself cannot
#: be used to discover which addresses are registered.
FORGOT_PASSWORD_PER_EMAIL = Limit("forgot:email", 3, HOUR)

#: Autodiscover requests per client IP. The endpoint is unauthenticated and
#: public by necessity — Outlook cannot present a credential before it knows
#: where the server is — so the limit is the only thing between it and a
#: script walking domain names. Generous enough for a real client, which
#: asks a handful of times while an account is being added.
AUTODISCOVER_PER_IP = Limit("autodiscover:ip", 30, QUARTER_HOUR)

#: Ownership verification and DNS health checks, per domain. Both resolve DNS
#: on our behalf, so this is an outbound-traffic control as much as an abuse one.
DOMAIN_CHECK_PER_DOMAIN = Limit("domain:check", 10, HOUR)

#: Mailbox creation per tenant.
MAILBOX_CREATE_PER_TENANT = Limit("mailbox:create", 20, HOUR)

#: Failed second-factor attempts per user, counted across every challenge token
#: that user holds. The per-challenge counter in apps.accounts.challenge caps
#: attempts against one token; this caps the attacker who simply asks for a new
#: token after five failures.
TWO_FACTOR_PER_USER = Limit("2fa:user", 5, QUARTER_HOUR)

#: Second-factor enrolment confirmations and 2FA-disable attempts, per user.
#: Both take a secret (a TOTP code, a password) and neither was limited before.
TWO_FACTOR_MANAGE_PER_USER = Limit("2fa:manage", 10, QUARTER_HOUR)

#: How long an accepted TOTP code stays un-replayable. pyotp is called with
#: valid_window=1, so a code is accepted for the timestep before, during and
#: after its own — 90 seconds. 120 covers that with margin for clock skew.
TOTP_REPLAY_TTL = 120

#: Platform Console login attempts per IP. Tighter than the tenant limit: the
#: population of platform administrators is a handful of people, so a legitimate
#: burst does not exist and anything that looks like one is an attack.
PLATFORM_LOGIN_PER_IP = Limit("platform:login:ip", 5, QUARTER_HOUR)

#: Platform Console login attempts per account.
PLATFORM_LOGIN_PER_ACCOUNT = Limit("platform:login:account", 5, QUARTER_HOUR)

#: Emailed security codes issued per account, counted across both login and
#: password reset. This is the mailbox-flooding control: without it, anyone who
#: knows a platform administrator's address can post the login form repeatedly
#: and fill their inbox.
PLATFORM_CODE_PER_ACCOUNT = Limit("platform:code:account", 5, QUARTER_HOUR)

#: Resend requests against one outstanding challenge.
PLATFORM_CODE_RESEND = Limit("platform:code:resend", 3, QUARTER_HOUR)

#: Platform password-reset requests per normalised email address. Counted for
#: every request, existing account or not, so the limit cannot be used to
#: discover who the platform administrators are.
PLATFORM_RESET_PER_EMAIL = Limit("platform:reset:email", 3, HOUR)

#: PostBox sign-in attempts per IP. Each one costs a real IMAP connection to
#: Dovecot, so unlike the Workspace login — where a failure is a local hash —
#: these are counted on every attempt rather than only on failure.
POSTBOX_SIGNIN_PER_IP = Limit("postbox:signin:ip", 10, QUARTER_HOUR)

#: PostBox sign-in attempts per mailbox address, from anywhere.
POSTBOX_SIGNIN_PER_ACCOUNT = Limit("postbox:signin:account", 8, QUARTER_HOUR)

#: Mailbox password changes per mailbox. The current password is required, so
#: this is a brute-force control on that check.
POSTBOX_PASSWORD_CHANGE = Limit("postbox:password:mailbox", 5, HOUR)

#: Mailbox searches. IMAP SEARCH without a full-text index is a server-side
#: scan, so an unbounded search box is an easy way to load the mail server.
POSTBOX_SEARCH_PER_MAILBOX = Limit("postbox:search:mailbox", 60, MINUTE)

#: Messages submitted from PostBox per mailbox. This does NOT replace the
#: engine's own per-mailbox rate limit, which still applies at submission —
#: it stops a runaway client before it reaches the mail path.
POSTBOX_SEND_PER_MAILBOX = Limit("postbox:send:mailbox", 60, HOUR)

#: Owner-recovery actions a platform administrator may trigger per hour. These
#: send mail to a customer, so an operator with a stuck script must not be able
#: to turn the console into a way of mailbombing an organization's owner.
OWNER_RECOVERY_PER_ADMIN = Limit("platform:recovery:admin", 20, HOUR)
