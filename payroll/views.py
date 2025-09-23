from io import BytesIO
import os
import re
import tempfile
from collections import defaultdict
from calendar import month_name
from datetime import datetime, timedelta, date, time
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.template.loader import get_template
from django.utils import timezone
from django.utils.timezone import is_naive, make_aware
from weasyprint import HTML
from attendance_app.models import *
from .models import EmployeeSalary
from django.http import HttpResponseForbidden

def is_not_attendance_group(user):
    return not user.groups.filter(name='attendance').exists()


def get_salary_summary_data(request, month_str, department_id=None, employee_id=None):
    
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        raise PermissionError("User has no company assigned")

    summary_data = []

    # Dropdowns (company-scoped)
    departments = Department.objects.filter(company=user_company)
    employees_qs = Employee.objects.filter(company=user_company).select_related('department', 'company')

    if department_id:
        employees_qs = employees_qs.filter(department__id=department_id, department__company=user_company)
    if employee_id:
        employees_qs = employees_qs.filter(id=employee_id, company=user_company)

    total_base_salary = Decimal(0)
    total_final_salary = Decimal(0)
    total_payable_cash = Decimal(0)

    if month_str:
        year, month = map(int, month_str.split('-'))
        start_date = datetime(year, month, 1).date()
        # মাসের শেষ দিন
        end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        # ---- helper: Department থেকে শিফট টাইম ----
        from datetime import time as _time
        DEFAULT_IN = _time(10, 30)
        DEFAULT_OUT = _time(20, 30)

        def _dept_times_for(emp, anchor_date):
            """
            Department থেকে (expected_start_time: time, regular_work_time: timedelta) বের করে।
            out_time < in_time হলে duration=0 ধরা হচ্ছে।
            রাত-পেরোনো শিফট চাইলে নিচের TODO আনকমেন্ট করো।
            """
            dep = getattr(emp, 'department', None)
            in_t = getattr(dep, 'in_time', None) if dep else None
            out_t = getattr(dep, 'out_time', None) if dep else None

            in_t = in_t or DEFAULT_IN
            out_t = out_t or DEFAULT_OUT

            dt_in = datetime.combine(anchor_date, in_t)
            dt_out = datetime.combine(anchor_date, out_t)
            duration = dt_out - dt_in
            if duration.total_seconds() < 0:
                # TODO (optional): রাত-পেরোনো শিফট সাপোর্ট
                # duration += timedelta(days=1)
                duration = timedelta(0)
            return in_t, duration
        # -------------------------------------------

        # Company-scoped public holidays
        holidays = Holiday.objects.filter(
            company=user_company,
            start_date__lte=end_date,
            end_date__gte=start_date
        )
        public_holiday_dates = set()
        for holiday in holidays:
            s = max(start_date, holiday.start_date)
            e = min(end_date, holiday.end_date)
            for i in range((e - s).days + 1):
                public_holiday_dates.add(s + timedelta(days=i))

        for emp in employees_qs:
            # Salary fetch
            try:
                sal = emp.employeesalary
                base_salary = sal.base_salary
                bank_transfer = sal.bank_transfer_amount
                cash = base_salary - bank_transfer
            except EmployeeSalary.DoesNotExist:
                continue

            # Attendance & Leave
            attendances = Attendance.objects.filter(
                employee=emp,
                timestamp__date__range=(start_date, end_date)
            ).order_by('timestamp')

            approved_leaves = LeaveRequest.objects.filter(
                company=user_company,
                employee=emp,
                status='Approved',
                start_date__lte=end_date,
                end_date__gte=start_date
            )

            # Expand leave dates into a set
            leave_dates = {
                lv.start_date + timedelta(days=i)
                for lv in approved_leaves
                for i in range((min(lv.end_date, end_date) - max(lv.start_date, start_date)).days + 1)
            }

            daily = defaultdict(list)
            for a in attendances:
                daily[a.timestamp.date()].append(a)

            off_day = emp.department.weekly_off_day if emp.department else None

            # >>> এখানে ডিপার্টমেন্ট থেকে শিফট সেট হচ্ছে <<<
            expected_start, regular = _dept_times_for(emp, start_date)

            total_days = (end_date - start_date).days + 1
            present_days = 0
            leave_days = 0
            weekly_off = 0
            pub_holiday = 0
            total_work_time = timedelta()
            total_late_time = timedelta()
            total_over_time = timedelta()

            # Working days = total - (weekly off + public holiday)
            working_days = 0
            for n in range(total_days):
                d = start_date + timedelta(days=n)
                if d in public_holiday_dates:
                    continue
                if off_day and d.strftime('%A') == off_day:
                    continue
                working_days += 1

                        # Per-day calc
            for n in range(total_days):
                d = start_date + timedelta(days=n)
                wd = d.strftime('%A')

                # Public holiday → exclude from expected & skip
                if d in public_holiday_dates:
                    pub_holiday += 1
                    continue

                # Weekly off → exclude from expected & skip
                if off_day and wd == off_day:
                    weekly_off += 1
                    continue

                recs = daily.get(d, [])
                ins = [r.timestamp for r in recs if r.status == 'In']
                outs = [r.timestamp for r in recs if r.status == 'Out']

                # --- Leave day ---
                if d in leave_dates:
                    leave_days += 1

                    # Default duration = 0, attendance থাকলে হিসাব করব
                    duration = timedelta()

                    if ins:
                        in_time = min(ins)
                        out_time = max(outs) if outs else None

                        if is_naive(in_time):
                            in_time = make_aware(in_time)

                        if out_time:
                            if is_naive(out_time):
                                out_time = make_aware(out_time)
                            adj_in = datetime.combine(in_time.date(), expected_start)
                            if is_naive(adj_in):
                                adj_in = make_aware(adj_in)
                            real_in = max(in_time, adj_in)
                            if out_time > real_in:
                                duration = out_time - real_in

                        # Late/OT হিসাব duration-এর উপরই
                        exp_dt = datetime.combine(in_time.date(), expected_start)
                        if is_naive(exp_dt):
                            exp_dt = make_aware(exp_dt)
                        if in_time > exp_dt:
                            total_late_time += in_time - exp_dt
                        if duration > regular:
                            total_over_time += duration - regular

                    # ✅ নতুন রুল: In বা Out যেকোনো একটা থাকলেই Present
                    if ins or outs:
                        present_days += 1

                    # ✅ Leave দিনে ক্রেডিট: max(duration, dept shift hours)
                    credited = max(duration, regular)
                    total_work_time += credited
                    continue

                # --- Normal day (not leave) ---
                # ✅ নতুন রুল: In বা Out যেকোনো একটা থাকলেই Present
                if ins or outs:
                    present_days += 1

                if ins:
                    in_time = min(ins)
                    out_time = max(outs) if outs else None

                    if is_naive(in_time):
                        in_time = make_aware(in_time)

                    dur = timedelta()
                    if out_time:
                        if is_naive(out_time):
                            out_time = make_aware(out_time)
                        adj_in = datetime.combine(in_time.date(), expected_start)
                        if is_naive(adj_in):
                            adj_in = make_aware(adj_in)
                        real_in = max(in_time, adj_in)
                        if out_time > real_in:
                            dur = out_time - real_in

                    total_work_time += dur

                    exp_dt = datetime.combine(in_time.date(), expected_start)
                    if is_naive(exp_dt):
                        exp_dt = make_aware(exp_dt)
                    if in_time > exp_dt:
                        total_late_time += in_time - exp_dt
                    if dur > regular:
                        total_over_time += dur - regular


            # Absent clamp (never negative)
            absent_days = max(0, working_days - present_days - leave_days)

            # Expected hours: weekly off + public holidays বাদ, leave বাদ নয়
            hours_per_day = max(regular.total_seconds() / 3600, 0)  # float
            expected_hours = working_days * hours_per_day  # hours

            # Actual hours (float)
            actual_hours = total_work_time.total_seconds() / 3600

            hourly_rate = (base_salary / Decimal(expected_hours)) if expected_hours > 0 else Decimal(0)

            # Earned salary: up to expected at 1x, extra at 1.5x
            if actual_hours <= expected_hours:
                earned_salary = Decimal(actual_hours) * hourly_rate
            else:
                extra = actual_hours - expected_hours
                earned_salary = (
                    Decimal(expected_hours) * hourly_rate
                    + Decimal(extra) * hourly_rate * Decimal('1.5')
                )

            # 🎁 BONUS: payout মাসে যোগ করো
            bonus_amount = sal.bonus_for_month(year, month)  # Decimal

            # Final salary = earned + bonus
            final_salary = earned_salary + bonus_amount

            # Payable cash = final - bank transfer
            payable_cash = final_salary - bank_transfer

            # Totals
            total_base_salary += base_salary
            total_final_salary += final_salary
            total_payable_cash += payable_cash

            # Formatting HH:MM:SS
            tot_sec = int(total_work_time.total_seconds())
            hh = tot_sec // 3600
            mm = (tot_sec % 3600) // 60
            ss = tot_sec % 60
            fmt_total_work = f"{hh:02d}:{mm:02d}:{ss:02d}"

            diff_sec = int((total_work_time - timedelta(hours=expected_hours)).total_seconds())
            dh = abs(diff_sec) // 3600
            dm = (abs(diff_sec) % 3600) // 60
            ds = abs(diff_sec) % 60
            sign = "-" if diff_sec < 0 else "+"
            fmt_diff = f"{sign}{dh:02d}:{dm:02d}:{ds:02d}"

            late_sec = int(total_late_time.total_seconds())
            lh = late_sec // 3600
            lm = (late_sec % 3600) // 60
            ls = int(late_sec % 60)
            fmt_late = f"{lh:02d}:{lm:02d}:{ls:02d}"

            summary_data.append({
                'employee': emp,
                'month': month_str,

                'base_salary': base_salary,
                'bank_transfer': round(bank_transfer, 2),
                'cash_amount': round(cash, 2),

                'present_days': present_days,
                'leave_days': leave_days,
                'absent_days': absent_days,
                'weekly_off_days': weekly_off,
                'holiday_days': pub_holiday,

                'total_work_hours': fmt_total_work,
                'expected_work_hours': f"{expected_hours:.0f}:00:00",
                'work_time_difference': fmt_diff,
                'late_time': fmt_late,
                'over_time': total_over_time,  # timedelta হিসেবেই রাখা

                'earned_salary': round(earned_salary, 2),  # (info only)
                'bonus_amount': round(bonus_amount, 2),     # 🎁 নতুন ফিল্ড
                'final_salary': round(final_salary, 2),     # earned + bonus
                'payable_cash': round(payable_cash, 2),
            })

    total_salary_difference = total_final_salary - total_base_salary
    total_bank_transfer = Decimal(0)
    total_cash_amount = Decimal(0)

    for row in summary_data:
        total_bank_transfer += row.get('bank_transfer', Decimal(0))
        total_cash_amount += row.get('cash_amount', Decimal(0))

    employees_dropdown = (
        Employee.objects.filter(company=user_company, department_id=department_id)
        if department_id else
        Employee.objects.filter(company=user_company)
    )

    return {
        'summaries': summary_data,
        'departments': departments,
        'employees': employees_dropdown,
        'selected_month': month_str,
        'selected_department': int(department_id) if department_id else None,
        'selected_employee': int(employee_id) if employee_id else None,
        'selected_department_id': int(department_id) if department_id else '',
        'selected_employee_id': int(employee_id) if employee_id else '',

        'total_base_salary': round(total_base_salary, 2),
        'total_final_salary': round(total_final_salary, 2),            # 🎁 bonus সহ
        'total_salary_difference': round(total_salary_difference, 2),  # (final - base)
        'total_bank_transfer': round(total_bank_transfer, 2),
        'total_cash_amount': round(total_cash_amount, 2),
        'total_payable_cash': round(total_payable_cash, 2),            # 🎁 bonus সহ
    }


@login_required
@user_passes_test(is_not_attendance_group)
def salary_summary_list(request):
    if request.user.groups.filter(name='attendance').exists():
        return redirect('dashboard')

    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        return HttpResponseForbidden("আপনার কোম্পানি সেট করা নেই।")

    month_str = request.GET.get('month')
    department_id = request.GET.get('department')
    employee_id = request.GET.get('employee')

    context = get_salary_summary_data(request, month_str, department_id, employee_id)
    return render(request, 'payroll/salary_summary_list.html', context)




@user_passes_test(is_not_attendance_group)
def export_salary_summary_pdf(request):
    # month (YYYY-MM), default current month
    month_str = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month_str):
        return HttpResponseBadRequest("Invalid month format. Use YYYY-MM.")

    # optional ids
    dep_raw = request.GET.get('department')
    emp_raw = request.GET.get('employee')
    try:
        department_id = int(dep_raw) if dep_raw else None
        employee_id = int(emp_raw) if emp_raw else None
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid department/employee id.")

    # ✅ pass request into your helper
    context = get_salary_summary_data(request, month_str, department_id, employee_id)
    context["print_mode"] = True  # চাইলে টেমপ্লেটে এই ফ্ল্যাগ চেক করতে পারেন

    # render template
    template = get_template('payroll/salary_summary_pdf.html')
    html_string = template.render(context)

    # make PDF (no external CSS passed here)
    pdf_io = BytesIO()
    HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf(target=pdf_io)
    pdf_io.seek(0)

    filename = f"salary_summary_{month_str}.pdf"
    resp = HttpResponse(pdf_io.read(), content_type='application/pdf')
    resp['Content-Disposition'] = f'inline; filename="{filename}"'
    return resp


# def is_not_attendance_group(user): ...

@login_required
@user_passes_test(is_not_attendance_group)
def set_base_salaries(request):
    """
    - Company scope
    - Database serial (Employee.id ASC) অনুযায়ী সাজানো
    - Search: q (name/device_user_id)
    - Pagination: per (default 30)
    - Bonus fields সাপোর্ট (থাকলে)
    """

    # company scope
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        messages.error(request, "আপনার প্রোফাইলে কোম্পানি সেট করা নেই।")
        return redirect("attendance_app:dashboard")

    # query params
    q = (request.GET.get("q") or "").strip()
    try:
        per = int(request.GET.get("per") or 30)
        if per <= 0 or per > 200:
            per = 30
    except ValueError:
        per = 30

    # base queryset (company-scoped), SERIAL ORDER = id ASC
    employees_qs = (
        Employee.objects.select_related("employeesalary", "department")
        .filter(company=user_company)
        .order_by("id")  # database serial asc
    )

    # search (name / device_user_id)
    if q:
        if q.isdigit():
            employees_qs = employees_qs.filter(
                Q(name__icontains=q) | Q(device_user_id=int(q))
            )
        else:
            employees_qs = employees_qs.filter(name__icontains=q)

    # pagination
    paginator = Paginator(employees_qs, per)
    page_obj = paginator.get_page(request.GET.get("page"))
    employees = page_obj.object_list  # শুধু এই পেজের এমপ্লয়ি

    # POST: শুধু current page-এর রেকর্ডগুলো সেভ হবে
    if request.method == "POST":
        updated, skipped, invalid = 0, 0, 0
        with transaction.atomic():
            for emp in employees:
                # field names
                k_base = f"salary_{emp.id}"
                k_bank = f"bank_transfer_{emp.id}"
                k_bperc = f"bonus_percent_{emp.id}"
                k_bfix  = f"bonus_fixed_{emp.id}"
                k_bmon  = f"bonus_month_{emp.id}"

                v_base  = (request.POST.get(k_base) or "").strip()
                v_bank  = (request.POST.get(k_bank) or "").strip()
                v_bperc = (request.POST.get(k_bperc) or "").strip()
                v_bfix  = (request.POST.get(k_bfix) or "").strip()
                v_bmon  = (request.POST.get(k_bmon) or "").strip()

                # সব ফাঁকা হলে স্কিপ
                if v_base == "" and v_bank == "" and v_bperc == "" and v_bfix == "" and v_bmon == "":
                    skipped += 1
                    continue

                # parse
                try:
                    base_salary = Decimal(v_base) if v_base != "" else Decimal("0")
                except InvalidOperation:
                    invalid += 1
                    continue

                try:
                    bank_transfer = Decimal(v_bank) if v_bank != "" else Decimal("0")
                except InvalidOperation:
                    bank_transfer = Decimal("0")

                try:
                    bonus_percent = Decimal(v_bperc) if v_bperc != "" else Decimal("0")
                except InvalidOperation:
                    bonus_percent = Decimal("0")

                try:
                    bonus_fixed = Decimal(v_bfix) if v_bfix != "" else Decimal("0")
                except InvalidOperation:
                    bonus_fixed = Decimal("0")

                try:
                    bonus_month = int(v_bmon) if v_bmon else 12
                    if bonus_month < 1 or bonus_month > 12:
                        bonus_month = 12
                except ValueError:
                    bonus_month = 12

                # clamp: bank ≤ base
                if bank_transfer > base_salary:
                    bank_transfer = base_salary
                    messages.warning(
                        request,
                        f"{emp.name}: Bank transfer base salary-এর চেয়ে বেশি হতে পারে না—adjusted."
                    )

                defaults = {
                    "company": user_company,
                    "base_salary": base_salary,
                    "bank_transfer_amount": bank_transfer,
                }
                # bonus ফিল্ড থাকলে তবেই সেট করবো
                if hasattr(EmployeeSalary, "yearly_bonus_percent"):
                    defaults["yearly_bonus_percent"] = bonus_percent
                if hasattr(EmployeeSalary, "yearly_bonus_fixed"):
                    defaults["yearly_bonus_fixed"] = bonus_fixed
                if hasattr(EmployeeSalary, "bonus_payout_month"):
                    defaults["bonus_payout_month"] = bonus_month

                EmployeeSalary.objects.update_or_create(employee=emp, defaults=defaults)
                updated += 1

        if updated:
            messages.success(request, f"✅ {updated} টি রেকর্ড (এই পেজ) আপডেট হয়েছে।")
        if skipped:
            messages.info(request, f"ℹ️ {skipped} টি রেকর্ডে কোনো পরিবর্তন ছিল না (স্কিপ)।")
        if invalid:
            messages.error(request, f"⚠️ {invalid} টি রেকর্ডে অবৈধ ইনপুট ছিল (স্কিপ)।")

        # একই পেজ/কুয়েরি তে ফিরে যাই
        return redirect(f"{request.path}?q={q}&per={per}&page={page_obj.number}")

    # months (value,label) for dropdown
    month_choices = [(i, month_name[i]) for i in range(1, 13)]

    return render(
    request,
    "payroll/set_base_salaries.html",
    {
        "employees": employees,
        "page_obj": page_obj,
        "q": q,
        "per": per,
        "month_choices": month_choices,
        "per_choices": [10, 20, 30, 50, 100],   # 👈 এখানে পাঠানো হলো
    },
)

