import json

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from attendance_app.models import Attendance, Department, Employee, Holiday, LeaveRequest
from payroll.models import EmployeeSalary


class Command(BaseCommand):
    help = (
        "একটা ডিপার্টমেন্টের কর্মচারী, হাজিরা, ছুটি, সরকারি ছুটি, বেতন ও অফিস টাইম JSON ফাইলে এক্সপোর্ট করে — "
        "লোকাল সার্ভারের ডাটা লাইভে নেওয়ার জন্য (লাইভে import_branch_data দিয়ে ঢোকান)"
    )

    def add_arguments(self, parser):
        parser.add_argument("--department", type=int, required=True, help="এই ডিপার্টমেন্ট id")
        parser.add_argument("--out", required=True, help="JSON ফাইলের নাম, যেমন bashundhara.json")

    def handle(self, *args, department, out, **options):
        dept = Department.objects.select_related("company").filter(pk=department).first()
        if not dept:
            choices = ", ".join(f"{d.id}={d.name}" for d in Department.objects.order_by("id"))
            raise CommandError(f"Department {department} not found. Available: {choices}")

        employees = list(Employee.objects.filter(department=dept).order_by("device_user_id"))
        uid_of = {e.id: e.device_user_id for e in employees}
        records = (Attendance.objects.filter(employee_id__in=uid_of)
                   .order_by("timestamp").values_list("employee_id", "timestamp", "status"))
        leaves = LeaveRequest.objects.filter(employee_id__in=uid_of).order_by("start_date")
        salaries = EmployeeSalary.objects.filter(employee_id__in=uid_of)
        # সরকারি ছুটি: কোম্পানিজুড়ে + এই ডিপার্টমেন্টের ব্রাঞ্চের
        holidays = Holiday.objects.filter(company=dept.company)
        if dept.branch_id:
            holidays = holidays.filter(branch__isnull=True) | holidays.filter(branch_id=dept.branch_id)

        data = {
            "format": "smartattendance-branch-export/2",
            "source": {"company": dept.company.name if dept.company else "", "department": dept.name,
                       "exported_at": timezone.now().isoformat()},
            "office_hours": {"weekly_off_day": dept.weekly_off_day,
                             "in_time": dept.in_time.strftime("%H:%M:%S") if dept.in_time else None,
                             "out_time": dept.out_time.strftime("%H:%M:%S") if dept.out_time else None},
            "employees": [{"uid": e.device_user_id, "name": e.name, "is_active": e.is_active} for e in employees],
            "attendance": [{"uid": uid_of[emp_id], "timestamp": ts.isoformat(), "status": status}
                           for emp_id, ts, status in records],
            "leaves": [{"uid": uid_of[l.employee_id], "leave_type": l.leave_type, "start_date": l.start_date.isoformat(),
                        "end_date": l.end_date.isoformat(), "reason": l.reason or "", "status": l.status}
                       for l in leaves],
            "holidays": [{"title": h.title, "start_date": h.start_date.isoformat(), "end_date": h.end_date.isoformat(),
                          "description": h.description or ""} for h in holidays.order_by("start_date")],
            "salaries": [{"uid": uid_of[s.employee_id], "base_salary": str(s.base_salary),
                          "bank_transfer_amount": str(s.bank_transfer_amount),
                          "yearly_bonus_percent": str(s.yearly_bonus_percent),
                          "yearly_bonus_fixed": str(s.yearly_bonus_fixed),
                          "bonus_payout_month": s.bonus_payout_month} for s in salaries],
        }
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.stdout.write(self.style.SUCCESS(
            f"{dept.name}: {len(data['employees'])} employees, {len(data['attendance'])} attendance, "
            f"{len(data['leaves'])} leaves, {len(data['holidays'])} holidays, {len(data['salaries'])} salaries → {out}"))
