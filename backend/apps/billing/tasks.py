import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="billing.expire_trials")
def expire_trials():
    """
    Run daily. Marks TRIALING subscriptions as PAST_DUE when trial_ends_at has passed.
    Existing mail flow continues — only new resource provisioning is blocked.
    """
    from .models import Subscription, SubscriptionStatus

    expired = Subscription.objects.filter(
        status=SubscriptionStatus.TRIALING,
        trial_ends_at__lt=timezone.now(),
    )
    count = expired.count()
    if count:
        expired.update(status=SubscriptionStatus.PAST_DUE)
        logger.info("Expired %d trial subscription(s).", count)
    return count
