# subscription_app/utils_limit.py
from datetime import date
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from subscription_app.models import UserSubscription  # ← তোমার path

def get_employee_limit_for(company, user=None) -> int:
    user = user or getattr(company, "owner", None)
    if not user:
        raise ValidationError(_("No user is linked to this company to check subscription."))

    today = date.today()
    sub = (UserSubscription.objects
           .filter(user=user, active=True, start_date__lte=today, end_date__gte=today)
           .order_by("-start_date")
           .first())
    if not sub:
        raise ValidationError(_("No active subscription found for this company."))

    limit = int(sub.plan.employee_limit)
    if limit <= 0:
        raise ValidationError(_("Your subscription plan does not allow adding employees."))
    return limit