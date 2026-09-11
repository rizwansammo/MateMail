"""
Refresh-token cookie.

The refresh token used to be returned in the JSON body and kept in
`localStorage`. Any script that ran on the page could read it, and a refresh
token is the durable credential: an access token expires in fifteen minutes,
a refresh token is good for seven days and can mint new access tokens for all
of them. One XSS meant a week of silent access, and clearing it required the
victim to log out from that browser.

It now lives in a cookie the browser will not hand to JavaScript.

Design, and why each part:

- **HttpOnly** — the entire point. `document.cookie` cannot see it.
- **Secure** in production. Off in development, where there is no TLS and the
  browser would simply drop the cookie.
- **SameSite=Strict.** The cookie is only ever read by same-origin `fetch`
  from our own app, and same-origin requests always carry Strict cookies. The
  usual reason to prefer Lax — keeping a session across a link from another
  site — does not apply, because the refresh happens by XHR after the page has
  loaded, not during the navigation itself. Strict is therefore free here, and
  it means no cross-site request of any kind can carry the token.
- **Path=/api/auth/.** The cookie is sent only to the endpoints that consume
  it. Every other API call — the overwhelming majority — carries no credential
  the browser attached on its own.
- **Max-Age matching the token's own lifetime**, so the browser drops it at
  the same moment the server would reject it.

CSRF: cookie authentication normally trades XSS exposure for CSRF exposure.
That trade is avoided here rather than accepted. The API itself is *not*
cookie-authenticated — every endpoint still requires a bearer access token in
an `Authorization` header, which a cross-site request cannot set. The cookie
authenticates exactly one operation, the token exchange at `/api/auth/refresh/`,
and SameSite=Strict already prevents any cross-site request from carrying it.
A forged refresh would in any case return its result into a response the
attacker's origin cannot read.

Residual exposure, stated plainly: the **access token is still readable by
JavaScript** (it is held in `sessionStorage`). An XSS can therefore still act
as the user for up to `ACCESS_TOKEN_LIFETIME` — fifteen minutes by default —
and cannot extend that window, because it cannot reach the refresh token.
Removing that last exposure means keeping the access token in memory only,
which costs a full re-authentication on every page load. It is not claimed to
be solved.
"""
from django.conf import settings

#: Cookie name. Deliberately not the old localStorage key: a browser holding a
#: stale `mm_refresh` localStorage entry and a new cookie must not be able to
#: confuse the two.
REFRESH_COOKIE_NAME = "matemail_refresh"

#: Only the endpoints that exchange or destroy the token receive it.
REFRESH_COOKIE_PATH = "/api/auth/"


def _max_age() -> int:
    return int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())


def set_refresh_cookie(response, token: str):
    """Attach the refresh token to `response` as an HttpOnly cookie."""
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=_max_age(),
        httponly=True,
        secure=getattr(settings, "REFRESH_COOKIE_SECURE", not settings.DEBUG),
        samesite=getattr(settings, "REFRESH_COOKIE_SAMESITE", "Strict"),
        path=REFRESH_COOKIE_PATH,
    )
    return response


def clear_refresh_cookie(response):
    """
    Remove the cookie.

    The path must match the one it was set with, or the browser keeps the
    original cookie and the user stays refreshable after logging out.
    """
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        samesite=getattr(settings, "REFRESH_COOKIE_SAMESITE", "Strict"),
    )
    return response


def read_refresh_cookie(request) -> str:
    """
    The caller's refresh token, or "" if absent.

    Deliberately the only way in. Accepting the token from the request body as
    well would leave two mechanisms, and the weaker one — a body field a script
    can populate — would define the security of the pair.
    """
    return request.COOKIES.get(REFRESH_COOKIE_NAME) or ""
