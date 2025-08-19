# subscription_app/utils.py
from datetime import datetime, time
from django.utils import timezone
from .models import UserSubscription

def _end_as_dt(end_value):
    tz = timezone.get_current_timezone()
    if isinstance(end_value, datetime):
        return end_value if timezone.is_aware(end_value) else timezone.make_aware(end_value, tz)
    return timezone.make_aware(datetime.combine(end_value, time(23, 59, 59, 999999)), tz)

def is_subscription_active(sub):
    if not sub:
        return False
    today = timezone.localdate()
    end_date = sub.end_date.date() if hasattr(sub.end_date, "date") else sub.end_date
    return end_date >= today and getattr(sub, "status", "active") != "cancelled"

