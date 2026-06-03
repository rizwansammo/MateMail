from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ForgotPasswordView,
    LoginView,
    LogoutView,
    MeView,
    ResendVerificationView,
    ResetPasswordView,
    SignupView,
    TwoFactorDisableView,
    TwoFactorSetupView,
    TwoFactorVerifyView,
    VerifyEmailView,
)

urlpatterns = [
    path("signup/", SignupView.as_view(), name="auth-signup"),
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
    path("resend-verification/", ResendVerificationView.as_view(), name="auth-resend-verification"),
    path("2fa/setup/", TwoFactorSetupView.as_view(), name="auth-2fa-setup"),
    path("2fa/verify/", TwoFactorVerifyView.as_view(), name="auth-2fa-verify"),
    path("2fa/disable/", TwoFactorDisableView.as_view(), name="auth-2fa-disable"),
    path("me/", MeView.as_view(), name="auth-me"),
]
