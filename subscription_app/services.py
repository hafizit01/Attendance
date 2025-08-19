# subscription_app/services.py
from datetime import timedelta
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .models import Subscription, UserSubscription, SubscriptionPlan


def _calc_period(
    plan: SubscriptionPlan,
    start_at: Optional[timezone.datetime] = None,
    extra_days: Optional[int] = None,
) -> Tuple[timezone.datetime, timezone.datetime]:
    """
    প্ল্যানের duration + extra_days ধরে start/end বের করে।
    start_at না দিলে এখন (timezone.now()) থেকে ধরা হবে।
    """
    start = start_at or timezone.now()
    duration = plan.duration_days or 30
    if extra_days:
        duration += int(extra_days)
    end = start + timedelta(days=duration)
    return start, end


def activate_or_renew_subscription(
    company,
    plan: SubscriptionPlan,
    start_at: Optional[timezone.datetime] = None,
    extra_days: Optional[int] = None,
) -> Subscription:
    """
    Company-লেভেল সাবস্ক্রিপশন: expires_at = start + plan.duration_days [+ extra_days]
    """
    start, end = _calc_period(plan, start_at, extra_days)
    sub, created = Subscription.objects.get_or_create(
        company=company,
        defaults={"plan": plan, "started_at": start, "expires_at": end, "status": "active"},
    )
    if created:
        sub.save()
        return sub

    sub.plan = plan
    sub.started_at = start
    sub.expires_at = end
    sub.status = "active"
    sub.save(update_fields=["plan", "started_at", "expires_at", "status"])
    return sub


def cancel_subscription(company) -> Optional[Subscription]:
    """
    Company সাবস্ক্রিপশন 'canceled' করুন।
    """
    try:
        sub = company.subscription
    except Subscription.DoesNotExist:
        return None
    sub.status = "canceled"
    sub.save(update_fields=["status"])
    return sub


def extend_subscription_days(company, days: int) -> Subscription:
    """
    Company সাবস্ক্রিপশনের মেয়াদ days দিন বাড়ান।
    """
    sub = company.subscription  # না থাকলে DoesNotExist উঠতে পারে—ইচ্ছাকৃত
    sub.expires_at = (sub.expires_at or timezone.now()) + timedelta(days=int(days))
    if sub.status in {"expired", "canceled"}:
        sub.status = "active"
    sub.save(update_fields=["expires_at", "status"])
    return sub


# -------------------- User-level subscription --------------------

def activate_user_subscription(
    user,
    plan: SubscriptionPlan,
    start_at: Optional[timezone.datetime] = None,
    extra_days: Optional[int] = None,
    carry_over: bool = True,
) -> UserSubscription:
    """
    User-লেভেল সাবস্ক্রিপশন একটিভ/রিনিউ করুন।
    carry_over=True হলে আগের end_date ভবিষ্যতে থাকলে তাতে যোগ হবে (stacking)।
    """
    start, end = _calc_period(plan, start_at, extra_days)
    start_d, end_d = start.date(), end.date()

    sub, created = UserSubscription.objects.get_or_create(
        user=user,
        defaults={"plan": plan, "start_date": start_d, "end_date": end_d, "active": True},
    )
    if created:
        sub.save()
        return sub

    if carry_over and sub.end_date and sub.end_date >= start_d:
        duration_days = (plan.duration_days or 30) + int(extra_days or 0)
        sub.end_date = sub.end_date + timedelta(days=duration_days)
        sub.start_date = start_d
    else:
        sub.start_date = start_d
        sub.end_date = end_d

    sub.plan = plan
    sub.active = True
    sub.save(update_fields=["plan", "start_date", "end_date", "active"])
    return sub


def deactivate_user_subscription(user) -> Optional[UserSubscription]:
    """
    User-লেভেল সাবস্ক্রিপশন de-activate করুন।
    """
    try:
        sub = UserSubscription.objects.get(user=user)
    except UserSubscription.DoesNotExist:
        return None
    sub.active = False
    sub.save(update_fields=["active"])
    return sub


# -------------------- One-call payment success handler --------------------

@transaction.atomic
def handle_payment_success(
    user,
    company,
    plan: SubscriptionPlan,
    start_at: Optional[timezone.datetime] = None,
    extra_days: Optional[int] = None,
):
    """
    bKash/Payment success → Company + User দুটো সাবস্ক্রিপশনই একসাথে আপডেট (atomic)।
    """
    company_sub = activate_or_renew_subscription(company, plan, start_at=start_at, extra_days=extra_days)
    user_sub = activate_user_subscription(user, plan, start_at=start_at, extra_days=extra_days, carry_over=True)
    return company_sub, user_sub
