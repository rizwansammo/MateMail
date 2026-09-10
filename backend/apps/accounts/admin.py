from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import EmailVerificationToken, PasswordResetToken, TwoFactorBackupCode, TwoFactorSetup, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "full_name", "is_platform_admin", "is_active", "created_at")
    list_filter = ("is_platform_admin", "is_active", "two_factor_enabled")
    search_fields = ("email", "full_name")
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "last_login")
    fieldsets = (
        (None, {"fields": ("id", "email", "password")}),
        ("Personal info", {"fields": ("full_name",)}),
        ("Status", {"fields": ("is_active", "email_verified", "two_factor_enabled")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "is_platform_admin", "groups", "user_permissions")}),
        ("Timestamps", {"fields": ("created_at", "last_login")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "password1", "password2")}),
    )


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "is_used", "expires_at", "created_at")
    list_filter = ("is_used",)
    search_fields = ("user__email",)
    readonly_fields = ("id", "created_at")
    exclude = ("token_hash",)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "is_used", "expires_at", "created_at")
    list_filter = ("is_used",)
    search_fields = ("user__email",)
    readonly_fields = ("id", "created_at")
    exclude = ("token_hash",)


@admin.register(TwoFactorSetup)
class TwoFactorSetupAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at")
    search_fields = ("user__email",)
    readonly_fields = ("id", "created_at")

    # totp_secret reconstructs the user's authenticator. Never render it.
    exclude = ("totp_secret",)


@admin.register(TwoFactorBackupCode)
class TwoFactorBackupCodeAdmin(admin.ModelAdmin):
    list_display = ("user", "is_used", "created_at")
    list_filter = ("is_used",)
    search_fields = ("user__email",)
    readonly_fields = ("id", "created_at")

    # Backup codes are short (8 chars, 36-symbol alphabet), so their unsalted
    # SHA-256 is brute-forceable offline. The hash has no admin use anyway.
    exclude = ("code_hash",)
