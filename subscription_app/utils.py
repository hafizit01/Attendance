# subscription_app/utils.py
# সাবস্ক্রিপশন চেকের একমাত্র জায়গা (single source of truth)।
# middleware, decorator, dashboard, login, employee limit — সবাই এখান থেকেই চেক করে।
from django.contrib.auth.models import User
from django.utils import timezone

from .models import UserSubscription


def get_user_company(user):
    """user -> profile -> company (না থাকলে None)"""
    return getattr(getattr(user, "profile", None), "company", None)


def _active_subscriptions():
    """active ফ্ল্যাগ True এবং আজকের তারিখ start_date..end_date এর মধ্যে"""
    today = timezone.localdate()
    return (
        UserSubscription.objects
        .select_related("plan")
        .filter(active=True, start_date__lte=today, end_date__gte=today)
        .order_by("-end_date")
    )


def get_company_subscription(company):
    """
    কোম্পানির সক্রিয় সাবস্ক্রিপশন: কোম্পানির owner বা কোম্পানির যেকোনো user
    যে প্ল্যান কিনেছে, সেটাই পুরো কোম্পানির জন্য প্রযোজ্য।
    """
    if not company:
        return None
    holder_ids = set(User.objects.filter(profile__company=company).values_list("id", flat=True))
    if company.owner_id:
        holder_ids.add(company.owner_id)
    return _active_subscriptions().filter(user_id__in=holder_ids).first()


def get_active_subscription(user):
    """
    user-এর সক্রিয় সাবস্ক্রিপশন:
    1) user.profile.company.subscription (কোম্পানি-লেভেল)
    2) না থাকলে user-এর নিজের সক্রিয় UserSubscription
    """
    if not user or not user.is_authenticated:
        return None
    company = get_user_company(user)
    sub = company.subscription if company else None
    if sub:
        return sub
    return _active_subscriptions().filter(user=user).first()


def is_subscription_active(user) -> bool:
    return get_active_subscription(user) is not None


def is_subscription_expired(user) -> bool:
    return not is_subscription_active(user)
