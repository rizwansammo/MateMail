from django.contrib import admin
from .models import Plan, Subscription, Invoice


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("display_name", "tier", "price_monthly", "max_domains", "max_mailboxes", "is_active")
    list_filter = ("tier", "is_active")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("tenant", "plan", "status", "trial_ends_at", "current_period_end")
    list_filter = ("status",)
    search_fields = ("tenant__name", "stripe_customer_id")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_number", "tenant", "amount", "currency", "status", "created_at")
    list_filter = ("status", "currency")
    search_fields = ("invoice_number", "tenant__name", "stripe_invoice_id")
    readonly_fields = ("id", "created_at")
