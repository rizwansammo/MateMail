"""
The Autodiscover URL, in the spellings real clients actually request.

Outlook builds this URL itself and does not agree with itself about case:
different versions and platforms send `/autodiscover/autodiscover.xml`,
`/Autodiscover/Autodiscover.xml` and `/AutoDiscover/AutoDiscover.xml`. Django's
resolver is case-sensitive, so each is listed rather than matched with a
case-insensitive pattern — an explicit list is greppable, cannot accidentally
widen to `/autodiscoverXanything`, and mirrors exactly what the nginx vhost
allow-lists.

They deliberately do NOT redirect to one canonical spelling. A 301 on a POST
lets a client drop the body and re-issue as GET, and Outlook's behaviour on a
redirected Autodiscover POST is not something to depend on.
"""
from django.urls import path

from .views import AutodiscoverView

_SPELLINGS = (
    "autodiscover/autodiscover.xml",
    "Autodiscover/Autodiscover.xml",
    "AutoDiscover/AutoDiscover.xml",
    "autodiscover/Autodiscover.xml",
    "Autodiscover/autodiscover.xml",
)

urlpatterns = [
    path(spelling, AutodiscoverView.as_view(), name=f"autodiscover-{index}")
    for index, spelling in enumerate(_SPELLINGS)
]
