from django.utils import timezone
from .models import UserSubscription

def get_effective_subscription(user):
    """
    company.subscription থাকলে সেটাই; না থাকলে ইউজারের latest active UserSubscription।
    """
    company = getattr(user, "current_company", None) or getattr(user, "company", None)
    sub = getattr(company, "subscription", None) if company else None
    if sub:
        return sub

    # Fallback: ইউজারের যেটা সত্যিই active আছে সেটি
    today = timezone.localdate()
    return (
        UserSubscription.objects
        .filter(user=user, active=True, end_date__gte=today)
        .order_by("-end_date")
        .first()
    )

def is_subscription_active_for(user) -> bool:
    sub = get_effective_subscription(user)
    if not sub:
        return False
    today = timezone.localdate()
    start_date = sub.start_date.date() if hasattr(sub.start_date, "date") else sub.start_date
    end_date   = sub.end_date.date()   if hasattr(sub.end_date, "date")   else sub.end_date
    return bool(getattr(sub, "active", True)) and start_date <= today <= end_date

def is_subscription_expired_for(user) -> bool:
    return not is_subscription_active_for(user)
