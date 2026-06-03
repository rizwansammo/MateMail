from django.db import models


class TenantScopedQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantScopedManager(models.Manager):
    """
    Default manager for every model that is owned by a tenant.
    Always call .for_tenant(request.tenant) before returning data to the API.
    Never return cross-tenant data.
    """

    def get_queryset(self):
        return TenantScopedQuerySet(self.model, using=self._db)

    def for_tenant(self, tenant):
        return self.get_queryset().for_tenant(tenant)
