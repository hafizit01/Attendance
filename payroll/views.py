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
from attendance_app.utils.params import int_param, month_param
from attendance_app.utils.pdf import render_pdf
from django.utils.http import url_has_allowed_host_and_scheme
from .forms import PayrollSettingsForm
from .rounding import round_cash_figure
from .overtime import DayWork, OvertimeSettings, calculate_pay
from attendance_app import scoping
from attendance_app.utils.attendance_helpers import load_holidays, is_holiday_for, load_transfers, branch_on
from django.http import HttpResponseForbidden
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
from django.db.models import Q, Prefetch
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.template.loader import get_template
from django.utils import timezone
from django.utils.timezone import is_naive, make_aware
from weasyprint import HTML
from attendance_app.models import *
from .models import EmployeeSalary

def is_not_attendance_group(user):
    return not user.groups.filter(name='attendance').exists()

from decimal import Decimal
from datetime import datetime, timedelta, time
from django.utils import timezone
from django.utils.timezone import is_naive, make_aware
from collections import defaultdict
def get_salary_summary_data(request, month_str, department_id=None, employee_id=None):
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        raise PermissionError("User has no company assigned")

    summary_data = []

    # Dropdowns (ব্রাঞ্চ ম্যানেজার / বাছাই করা ব্রাঞ্চ অনুযায়ী)
    scope = scoping.get_scope(request)
    departments = scoping.departments(Department.objects.all(), scope)
    employees_qs = Employee.objects.filter(company=user_company).select_related('department', 'department__branch', 'employeesalary', 'company').order_by('id')

    if department_id:
        employees_qs = employees_qs.filter(department__id=department_id)
    if employee_id:
        employees_qs = employees_qs.filter(id=employee_id)

    # Accumulators
    total_base_salary = Decimal(0)
    total_final_salary = Decimal(0)
    total_payable_cash = Decimal(0)
    
    # Extra Totals needed for Footer
    total_earned = Decimal(0)
    total_bonus = Decimal(0)
    total_rounding_adjustment = Decimal(0)
    rounding_enabled = user_company.salary_rounding_enabled
    ot_settings = OvertimeSettings.for_company(user_company)

    if month_str:
        year, month = map(int, month_str.split('-'))
        start_date = datetime(year, month, 1).date()
        next_month = start_date.replace(day=28) + timedelta(days=4)
        end_date = next_month - timedelta(days=next_month.day)

        # Fetch Data
        all_attendance = Attendance.objects.filter(
            employee__in=employees_qs,
            timestamp__date__range=(start_date, end_date)
        ).order_by('timestamp')

        all_leaves = LeaveRequest.objects.filter(
            company=user_company, status='Approved',
            start_date__lte=end_date, end_date__gte=start_date
        )

        # Optimization Maps
        att_map = defaultdict(list)
        for att in all_attendance:
            att_map[(att.employee_id, timezone.localtime(att.timestamp).date())].append(att)

        leave_map = set()
        for lv in all_leaves:
            s = max(lv.start_date, start_date)
            e = min(lv.end_date, end_date)
            curr = s
            while curr <= e:
                leave_map.add((lv.employee_id, curr))
                curr += timedelta(days=1)

        # ছুটি: সব-ব্রাঞ্চের + কর্মচারী যে দিন যে ব্রাঞ্চে ছিল সেই ব্রাঞ্চের
        company_holidays = load_holidays([user_company.id], start_date, end_date).get(user_company.id, {})
        emp_transfers = load_transfers(list(employees_qs.values_list('id', flat=True)))

        DEFAULT_IN = time(10, 30)
        DEFAULT_OUT = time(20, 30)

        for emp in employees_qs:
            if not hasattr(emp, 'employeesalary'):
                continue
            current_branch = emp.department.branch_id if emp.department_id else None
            transfers = emp_transfers.get(emp.id, [])
            # ব্রাঞ্চ বাছাই থাকলে: মাসের শেষ দিনে যে ব্রাঞ্চে ছিল সেই ব্রাঞ্চের বেতনে ধরা হয়
            if scope.branch and branch_on(current_branch, transfers, end_date) != scope.branch_id:
                continue
            
            sal = emp.employeesalary
            base_salary = sal.base_salary
            bank_transfer = sal.bank_transfer_amount
            # Cash cannot be negative (শুধুমাত্র বেসিক থেকে ব্যাংক বাদ)
            base_cash = max(base_salary - bank_transfer, Decimal(0))

            dep = emp.department
            off_day = dep.weekly_off_day if dep else None
            in_t = dep.in_time if dep and dep.in_time else DEFAULT_IN
            out_t = dep.out_time if dep and dep.out_time else DEFAULT_OUT
            
            # Shift Calculation
            dt_in = datetime.combine(start_date, in_t)
            dt_out = datetime.combine(start_date, out_t)
            if out_t < in_t:
                dt_out += timedelta(days=1)
            regular = dt_out - dt_in
            
            # Counters
            present_days = 0
            leave_days = 0
            weekly_off = 0
            pub_holiday = 0
            absent_days = 0
            
            total_late_time = timedelta()
            day_works = []  # ওভারটাইম/বেতনের হিসাবের জন্য প্রতিদিনের কাজ (payroll/overtime.py)

            total_days = (end_date - start_date).days + 1
            working_days_count = 0

            for n in range(total_days):
                curr = start_date + timedelta(days=n)
                wd = curr.strftime('%A')

                is_holiday = is_holiday_for(company_holidays, curr, branch_on(current_branch, transfers, curr))
                is_off = (off_day and wd == off_day)
                is_leave = (emp.id, curr) in leave_map
                recs = att_map.get((emp.id, curr), [])

                if is_holiday:
                    pub_holiday += 1
                    if not recs:
                        continue
                elif is_off:
                    weekly_off += 1
                    if not recs:
                        continue
                else:
                    working_days_count += 1

                if is_leave:
                    leave_days += 1
                    day_works.append(DayWork(is_leave=True))
                    continue

                if recs:
                    timestamps = [r.timestamp for r in recs]
                    first_in = min(timestamps)
                    last_out = max(timestamps) if len(timestamps) > 1 else None
                    present_days += 1
                    
                    # Late Logic
                    exp_in = datetime.combine(curr, in_t)
                    if is_naive(exp_in): exp_in = make_aware(exp_in)
                    if is_naive(first_in): first_in = make_aware(first_in)
                    
                    if first_in > exp_in:
                        total_late_time += (first_in - exp_in)

                    # Work Duration and Overtime
                    if last_out:
                        if is_naive(last_out): last_out = make_aware(last_out)
                        actual_start = max(first_in, exp_in) 
                        if last_out > actual_start:
                            day_works.append(DayWork(work=last_out - actual_start, is_off=bool(is_holiday or is_off)))
                else:
                    absent_days += 1

            # Calculations (ওভারটাইম কোম্পানির সেটিং অনুযায়ী)
            pay = calculate_pay(base_salary, regular, working_days_count, day_works, ot_settings)
            total_work_time = pay['total_work']
            total_over_time = pay['over_time']
            expected_hours_float = pay['expected_hours']
            expected_td = timedelta(hours=expected_hours_float)

            diff_seconds = total_work_time.total_seconds() - expected_td.total_seconds()
            
            abs_seconds = abs(diff_seconds)
            h = int(abs_seconds // 3600)
            m = int((abs_seconds % 3600) // 60)
            sign = "+" if diff_seconds >= 0 else "-"
            time_diff_str = f"{sign}{h:02d}:{m:02d}"

            # Salary Logic
            earned_salary = pay['earned']

            bonus_amount = sal.bonus_for_month(year, month)
            final_salary = earned_salary + bonus_amount
            
            # 🟢 আপনার দেওয়া লজিক অনুযায়ী পেয়াবল ক্যাশ ক্যালকুলেশন
            if base_salary > 0:
                # ব্যাংকের অংশে কত টাকা বোনাস পড়বে তা আনুপাতিক হারে বের করা হচ্ছে (e.g., 25k/30k = 83.33%)
                bonus_to_bank = bonus_amount * (bank_transfer / base_salary)
            else:
                bonus_to_bank = Decimal(0)
            
            # ব্যাংকে একচুয়ালি পাঠানো মোট টাকা (বেসিক ব্যাংক + বোনাসের ব্যাংক অংশ)
            actual_bank_transfer = bank_transfer + bonus_to_bank
            
            # ফাইনাল পেয়াবল ক্যাশ = মোট ফাইনাল স্যালারি থেকে ব্যাংকের মোট টাকা বাদ
            # (অ্যাবসেন্ট হলে ক্যাশ থেকে কাটা যাবে, ওভারটাইম হলে ক্যাশে যোগ হবে)
            payable_cash = max(final_salary - actual_bank_transfer, Decimal(0))

            # ফিগার মিল: সেটিং চালু থাকলে নগদ অংশ 50/100 টাকায় (সবসময় উপরে) — ব্যাংক অংশ হুবহু থাকে
            payable_cash_raw = payable_cash
            rounding_adjustment = Decimal(0)
            if rounding_enabled:
                payable_cash = round_cash_figure(payable_cash_raw)
                rounding_adjustment = payable_cash - payable_cash_raw
                final_salary += rounding_adjustment  # মোট = ব্যাংক + (মেলানো) নগদ

            salary_diff = final_salary - base_salary

            # Totals
            total_base_salary += base_salary
            total_final_salary += final_salary
            total_payable_cash += payable_cash
            total_rounding_adjustment += rounding_adjustment
            total_earned += earned_salary
            total_bonus += bonus_amount

            def fmt_td(td):
                s = int(td.total_seconds())
                return f"{s//3600:02d}:{(s%3600)//60:02d}"

            summary_data.append({
                'employee': emp,
                'month': month_str,
                'base_salary': base_salary,
                'bank_transfer': bank_transfer,
                'cash_amount': base_cash, # Pure Base Cash (30k-25k=5k)
                
                'present_days': present_days,
                'leave_days': leave_days,
                'absent_days': absent_days,
                'weekly_off_days': weekly_off,
                'holiday_days': pub_holiday,
                
                'late_time': fmt_td(total_late_time),
                'over_time': fmt_td(total_over_time),
                
                'expected_hours': f"{int(expected_hours_float):02d}:00",
                'total_work_hours': fmt_td(total_work_time),
                
                'time_difference_val': diff_seconds, 
                'time_difference_str': time_diff_str,
                
                'earned_salary': earned_salary,
                'overtime_pay': pay['overtime_pay'],
                'bonus_amount': bonus_amount,
                'final_salary': final_salary,
                'payable_cash': payable_cash, # 🟢 আপনার লজিকের ফাইনাল ক্যাশ (7500 tk)
                'payable_cash_raw': payable_cash_raw,
                'rounding_adjustment': rounding_adjustment,
                
                'salary_difference': salary_diff
            })

    # Footer Totals
    total_salary_difference = total_final_salary - total_base_salary
    total_bank_sum = sum(row['bank_transfer'] for row in summary_data)
    total_cash_amount_sum = sum(row['cash_amount'] for row in summary_data)

    employees_dropdown = scoping.employees(Employee.objects.all(), scope)
    if department_id:
        employees_dropdown = employees_dropdown.filter(department_id=department_id)

    return {
        'summaries': summary_data,
        'departments': departments,
        'branch_name': scope.branch.name if scope.branch else '',
        'employees': employees_dropdown,
        'selected_month': month_str,
        'selected_department': int(department_id) if department_id else None,
        'selected_employee': int(employee_id) if employee_id else None,
        
        'total_base_salary': round(total_base_salary, 2),
        'total_bank_transfer': round(total_bank_sum, 2),
        'total_cash_amount': round(total_cash_amount_sum, 2),
        'total_payable_cash': round(total_payable_cash, 2),
        
        'total_earned': round(total_earned, 2),
        'total_bonus': round(total_bonus, 2),
        'total_final_salary': round(total_final_salary, 2),
        'total_salary_difference': round(total_salary_difference, 2),

        'rounding_enabled': rounding_enabled,
        'total_rounding_adjustment': round(total_rounding_adjustment, 2),
        'can_change_settings': can_change_payroll_settings(request.user, user_company),
        'ot_settings': ot_settings,
        'company': user_company,
    }
    
def can_change_payroll_settings(user, company):
    """বেতন সেটিং বদলাতে পারে: superuser, কোম্পানির owner, অথবা change_company পারমিশন থাকলে"""
    if not company:
        return False
    return (user.is_superuser or company.owner_id == user.id
            or user.has_perm('attendance_app.change_company'))


@login_required
def payroll_settings(request):
    """ফিগার মিল + ওভারটাইম সেটিং (কোম্পানি অনুযায়ী)"""
    company = getattr(getattr(request.user, "profile", None), "company", None)
    if not can_change_payroll_settings(request.user, company):
        return HttpResponseForbidden("এই সেটিং বদলানোর অনুমতি আপনার নেই।")

    form = PayrollSettingsForm(request.POST or None, instance=company)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, "বেতন সেটিং সেভ হয়েছে।")
        nxt = request.POST.get('next')
        if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
            return redirect(nxt)
        return redirect('payroll:salary_summary_list')

    return render(request, 'payroll/payroll_settings.html', {
        'form': form,
        'next': request.GET.get('next') or request.POST.get('next') or '',
    })


@login_required
def salary_summary_list(request):
    if request.user.groups.filter(name='attendance').exists():
        return redirect('attendance_app:dashboard')

    # ভুল ইনপুট হলে 400 (আগে 500 দিত)
    month_str = month_param(request.GET, 'month')
    department_id = int_param(request.GET, 'department')
    employee_id = int_param(request.GET, 'employee')

    try:
        context = get_salary_summary_data(request, month_str, department_id, employee_id)
    except PermissionError:
        return HttpResponseForbidden("Company not set.")

    return render(request, 'payroll/salary_summary_list.html', context)

@login_required
def export_salary_summary_pdf(request):
    # ভুল ইনপুট হলে 400 (HTML পেজের মতোই); মাস না দিলে চলতি মাস
    month_str = month_param(request.GET, 'month', timezone.localdate().strftime('%Y-%m'))
    department_id = int_param(request.GET, 'department')
    employee_id = int_param(request.GET, 'employee')

    try:
        context = get_salary_summary_data(request, month_str, department_id, employee_id)
    except PermissionError:
        return HttpResponseForbidden("আপনার কোম্পানি সেট করা নেই।")

    month = datetime.strptime(context['selected_month'], '%Y-%m')
    dept = next((d for d in context['departments'] if d.id == department_id), None) if department_id else None
    emp = context['employees'].filter(id=employee_id).first() if employee_id else None
    context.update({
        'month_label': month.strftime('%B %Y'),
        'department_name': dept.name if dept else '',
        'employee_name': emp.name if emp else '',
        'total_salary_difference_abs': abs(context['total_salary_difference']),
    })
    return render_pdf(request, 'payroll/salary_summary_pdf.html', context, f"Salary_Summary_{context['selected_month']}.pdf")
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
        scoping.employees(Employee.objects.select_related("employeesalary", "department"), scoping.get_scope(request))
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

