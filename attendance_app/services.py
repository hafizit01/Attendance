from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from subscription_app.utils_limit import get_employee_limit_for
from .models import Employee

@transaction.atomic
def create_employee_with_limit(*, company, name, device_user_id,
                               department=None, user=None, sub_user=None) -> Employee:
    company.__class__.objects.select_for_update().get(pk=company.pk)

    # sub_user: যেই ইউজারের subscription কার্যকর (e.g., request.user)
    limit = get_employee_limit_for(company, user=sub_user)

    qs = Employee.objects.filter(company=company)
    if any(f.name == "is_active" for f in Employee._meta.get_fields()):
        qs = qs.filter(is_active=True)
    current = qs.count()

    if limit is not None and current >= limit:
        raise ValidationError(f"Employee limit reached ({current}/{limit}).")

    return Employee.objects.create(
        company=company, name=name, device_user_id=device_user_id,
        department=department, user=user
    )


@transaction.atomic
def activate_employee_with_limit(*, employee: Employee) -> Employee:
    if getattr(employee, "is_active", None) is False:
        # কোম্পানি রো লক
        _ = employee.company.__class__.objects.select_for_update().get(pk=employee.company_id)

        limit = get_employee_limit_for(employee.company)

        qs = Employee.objects.filter(company=employee.company, is_active=True)
        current = qs.exclude(pk=employee.pk).count()

        if limit is not None and current >= limit:
            raise ValidationError(
                _(f"Employee limit reached ({current}/{limit}). Upgrade your plan to activate more employees.")
            )

        employee.is_active = True
        employee.save(update_fields=["is_active"])
    return employee
