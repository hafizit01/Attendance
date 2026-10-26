# subscription_app/utils_limit.py
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from subscription_app.utils import get_company_subscription

def get_employee_limit_for(company, user=None) -> int:
    if not company:
        raise ValidationError(_("No company found to check subscription."))

    sub = get_company_subscription(company)
    if not sub:
        raise ValidationError(_("No active subscription found for this company."))

    limit = int(sub.plan.employee_limit)
    if limit <= 0:
        raise ValidationError(_("Your subscription plan does not allow adding employees."))
    return limit
