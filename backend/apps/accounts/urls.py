from django.urls import path

from .recovery_views import (
    RecoveryEmailSetupView, RecoveryEmailConfirmView,
    RecoveryLoginStartView, RecoveryLoginFinishView,
)
from .required_enrollment import RequiredTwoFactorEnrollmentView
from .views import (
    ForgotPasswordView,
    LoginView,
    LogoutView,
    MeView,
    RefreshView,
    ResendVerificationView,
    ResetPasswordView,
    SignupView,
    TwoFactorDisableView,
    TwoFactorSetupView,
    TwoFactorBackupCodeRegenerateView,
    TwoFactorVerifyView,
    VerifyEmailView,
)

urlpatterns = [
    path("signup/", SignupView.as_view(), name="auth-signup"),
    path("login/", LoginView.as_view(), name="auth-login"),
    path("logout/", LogoutView.as_view(), name="auth-logout"),
    path("refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("forgot-password/", ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("reset-password/", ResetPasswordView.as_view(), name="auth-reset-password"),
    path("verify-email/", VerifyEmailView.as_view(), name="auth-verify-email"),
    path("resend-verification/", ResendVerificationView.as_view(), name="auth-resend-verification"),
    path("2fa/setup/", TwoFactorSetupView.as_view(), name="auth-2fa-setup"),
    path("2fa/backup-codes/regenerate/", TwoFactorBackupCodeRegenerateView.as_view(), name="auth-2fa-backup-codes-regenerate"),
    path("2fa/verify/", TwoFactorVerifyView.as_view(), name="auth-2fa-verify"),
    path("2fa/enroll-required/", RequiredTwoFactorEnrollmentView.as_view(), name="auth-2fa-enroll-required"),
    path("recovery-email/", RecoveryEmailSetupView.as_view(), name="auth-recovery-email"),
    path("recovery-email/confirm/", RecoveryEmailConfirmView.as_view(), name="auth-recovery-confirm"),
    path("2fa/recovery/start/", RecoveryLoginStartView.as_view(), name="auth-2fa-recovery-start"),
    path("2fa/recovery/finish/", RecoveryLoginFinishView.as_view(), name="auth-2fa-recovery-finish"),
    path("2fa/disable/", TwoFactorDisableView.as_view(), name="auth-2fa-disable"),
    path("me/", MeView.as_view(), name="auth-me"),
]
