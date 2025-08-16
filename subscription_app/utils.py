# subscription_app/utils.py
from datetime import datetime, time
from django.utils import timezone
from .models import UserSubscription

def _end_as_dt(end_value):
    tz = timezone.get_current_timezone()
    if isinstance(end_value, datetime):
        return end_value if timezone.is_aware(end_value) else timezone.make_aware(end_value, tz)
    return timezone.make_aware(datetime.combine(end_value, time(23, 59, 59, 999999)), tz)

def is_subscription_active(user) -> bool:
    last = (UserSubscription.objects
            .filter(user=user)
            .order_by("-end_date")
            .first())
    if not last or not last.end_date:
        return False
    return timezone.now() <= _end_as_dt(last.end_date)
