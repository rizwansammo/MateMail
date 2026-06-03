from django.urls import path

from .views import WebmailSSOView, WebmailTokenValidateView

urlpatterns = [
    path("sso/",            WebmailSSOView.as_view(),           name="webmail-sso"),
    path("validate-token/", WebmailTokenValidateView.as_view(), name="webmail-validate-token"),
]
