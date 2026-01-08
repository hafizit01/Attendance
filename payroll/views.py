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

def get_salary_summary_data(request, month_str, department_id=None, employee_id=None):
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        raise PermissionError("User has no company assigned")

    summary_data = []

    # Dropdowns (company-scoped)
    departments = Department.objects.filter(company=user_company)
    
    # ✅ Optimized Query: prefetch_related ব্যবহার করে একবারে ডাটা আনা
    employees_qs = Employee.objects.filter(company=user_company).select_related('department', 'employeesalary', 'company')

    if department_id:
        employees_qs = employees_qs.filter(department__id=department_id)
    if employee_id:
        employees_qs = employees_qs.filter(id=employee_id)

    total_base_salary = Decimal(0)
    total_final_salary = Decimal(0)
    total_payable_cash = Decimal(0)

    if month_str:
        year, month = map(int, month_str.split('-'))
        start_date = datetime(year, month, 1).date()
        # মাসের শেষ দিন বের করা
        next_month = start_date.replace(day=28) + timedelta(days=4)
        end_date = next_month - timedelta(days=next_month.day)

        # ✅ Optimized Fetching: লুপের বাইরে ডাটা আনা
        all_attendance = Attendance.objects.filter(
            employee__in=employees_qs,
            timestamp__date__range=(start_date, end_date)
        ).order_by('timestamp')

        all_leaves = LeaveRequest.objects.filter(
            company=user_company,
            status='Approved',
            start_date__lte=end_date,
            end_date__gte=start_date
        )

        # Mappings for O(1) access
        att_map = defaultdict(list)
        for att in all_attendance:
            att_map[(att.employee_id, att.timestamp.date())].append(att)

        leave_map = set()
        for lv in all_leaves:
            s = max(lv.start_date, start_date)
            e = min(lv.end_date, end_date)
            curr = s
            while curr <= e:
                leave_map.add((lv.employee_id, curr))
                curr += timedelta(days=1)

        # Holidays
        holidays = Holiday.objects.filter(
            company=user_company,
            start_date__lte=end_date,
            end_date__gte=start_date
        )
        holiday_dates = set()
        for h in holidays:
            s = max(h.start_date, start_date)
            e = min(h.end_date, end_date)
            curr = s
            while curr <= e:
                holiday_dates.add(curr)
                curr += timedelta(days=1)

        # Helper: Shift Times
        DEFAULT_IN = time(10, 30)
        DEFAULT_OUT = time(20, 30)

        for emp in employees_qs:
            # Salary Check
            if not hasattr(emp, 'employeesalary'):
                continue
            
            sal = emp.employeesalary
            base_salary = sal.base_salary
            bank_transfer = sal.bank_transfer_amount
            cash = max(base_salary - bank_transfer, Decimal(0))

            dep = emp.department
            off_day = dep.weekly_off_day if dep else None
            
            # Shift Calculation
            in_t = dep.in_time if dep and dep.in_time else DEFAULT_IN
            out_t = dep.out_time if dep and dep.out_time else DEFAULT_OUT
            
            # ✅ Fix: Night Shift Logic
            dt_in = datetime.combine(start_date, in_t)
            dt_out = datetime.combine(start_date, out_t)
            if out_t < in_t:
                dt_out += timedelta(days=1) # শিফট পরের দিনে গেছে
            
            regular = dt_out - dt_in
            
            # Counters
            present_days = 0
            leave_days = 0
            weekly_off = 0
            pub_holiday = 0
            absent_days = 0
            
            total_work_time = timedelta()
            total_late_time = timedelta()
            total_over_time = timedelta()

            total_days = (end_date - start_date).days + 1
            working_days_count = 0

            for n in range(total_days):
                curr = start_date + timedelta(days=n)
                wd = curr.strftime('%A')

                # Priority Checks
                is_holiday = curr in holiday_dates
                is_off = (off_day and wd == off_day)
                is_leave = (emp.id, curr) in leave_map
                recs = att_map.get((emp.id, curr), [])

                if is_holiday:
                    pub_holiday += 1
                    continue
                
                if is_off:
                    weekly_off += 1
                    continue
                
                working_days_count += 1 # এটা ওয়ার্কিং ডে ছিল

                if is_leave:
                    leave_days += 1
                    total_work_time += regular # লিভের জন্য ফুল টাইম ক্রেডিট
                    continue

                if recs:
                    # ✅ Logic: First In - Last Out
                    timestamps = [r.timestamp for r in recs]
                    first_in = min(timestamps)
                    last_out = max(timestamps) if len(timestamps) > 1 else None
                    
                    present_days += 1
                    
                    # Late Calc
                    exp_in = datetime.combine(curr, in_t)
                    if is_naive(exp_in): exp_in = make_aware(exp_in)
                    if is_naive(first_in): first_in = make_aware(first_in)
                    
                    if first_in > exp_in:
                        total_late_time += (first_in - exp_in)

                    # Work Duration
                    if last_out:
                        if is_naive(last_out): last_out = make_aware(last_out)
                        
                        # Adjust start time (যদি লেটে আসে, তবুও শিফট শুরু থেকে ধরা হবে না, একচুয়াল ইন টাইম ধরা হবে)
                        actual_start = max(first_in, exp_in) 
                        if last_out > actual_start:
                            duration = last_out - actual_start
                            total_work_time += duration
                            
                            if duration > regular:
                                total_over_time += (duration - regular)
                else:
                    absent_days += 1

            # --- Salary Calculation Logic ---
            
            # Expected Hours (ছুটি বাদে)
            hours_per_day = regular.total_seconds() / 3600
            expected_hours = working_days_count * hours_per_day
            
            actual_hours = total_work_time.total_seconds() / 3600
            
            # ✅ Fix: Zero Division Error
            hourly_rate = (base_salary / Decimal(expected_hours)) if expected_hours > 0 else Decimal(0)

            # Earned Salary Logic (1.5x OT)
            if actual_hours <= expected_hours:
                earned_salary = Decimal(actual_hours) * hourly_rate
            else:
                extra = actual_hours - expected_hours
                earned_salary = (Decimal(expected_hours) * hourly_rate) + (Decimal(extra) * hourly_rate * Decimal('1.5'))

            # Bonus
            bonus_amount = sal.bonus_for_month(year, month)
            final_salary = earned_salary + bonus_amount
            payable_cash = max(final_salary - bank_transfer, Decimal(0))

            # Accumulate Totals
            total_base_salary += base_salary
            total_final_salary += final_salary
            total_payable_cash += payable_cash

            # Format Durations
            def fmt_td(td):
                s = int(td.total_seconds())
                return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

            summary_data.append({
                'employee': emp,
                'month': month_str,
                'base_salary': base_salary,
                'bank_transfer': bank_transfer,
                'cash_amount': cash,
                'present_days': present_days,
                'leave_days': leave_days,
                'absent_days': absent_days,
                'weekly_off_days': weekly_off,
                'holiday_days': pub_holiday,
                'total_work_hours': fmt_td(total_work_time),
                'expected_work_hours': f"{int(expected_hours):02d}:00:00",
                'work_time_difference': fmt_td(total_work_time - timedelta(hours=expected_hours)),
                'late_time': fmt_td(total_late_time),
                'over_time': total_over_time,
                'earned_salary': round(earned_salary, 2),
                'bonus_amount': round(bonus_amount, 2),
                'final_salary': round(final_salary, 2),
                'payable_cash': round(payable_cash, 2),
            })

    # Totals for Footer
    total_salary_difference = total_final_salary - total_base_salary
    total_bank_sum = sum(row['bank_transfer'] for row in summary_data)
    total_cash_sum = sum(row['payable_cash'] for row in summary_data)

    employees_dropdown = Employee.objects.filter(company=user_company)
    if department_id:
        employees_dropdown = employees_dropdown.filter(department_id=department_id)

    return {
        'summaries': summary_data,
        'departments': departments,
        'employees': employees_dropdown,
        'selected_month': month_str,
        'selected_department': int(department_id) if department_id else None,
        'selected_employee': int(employee_id) if employee_id else None,
        'total_base_salary': round(total_base_salary, 2),
        'total_final_salary': round(total_final_salary, 2),
        'total_salary_difference': round(total_salary_difference, 2),
        'total_bank_transfer': round(total_bank_sum, 2),
        'total_payable_cash': round(total_cash_sum, 2),
    }

@login_required
@user_passes_test(is_not_attendance_group)
def salary_summary_list(request):
    if request.user.groups.filter(name='attendance').exists():
        return redirect('dashboard')

    month_str = request.GET.get('month')
    department_id = request.GET.get('department')
    employee_id = request.GET.get('employee')

    try:
        context = get_salary_summary_data(request, month_str, department_id, employee_id)
    except PermissionError:
        return HttpResponseForbidden("Company not set.")
        
    return render(request, 'payroll/salary_summary_list.html', context)



@user_passes_test(is_not_attendance_group)
def export_salary_summary_pdf(request):
    # 1. Month Validation
    month_str = request.GET.get('month') or timezone.localdate().strftime('%Y-%m')
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month_str):
        return HttpResponseBadRequest("Invalid month format. Use YYYY-MM.")

    # 2. Department/Employee Validation
    dep_raw = request.GET.get('department')
    emp_raw = request.GET.get('employee')
    try:
        department_id = int(dep_raw) if dep_raw else None
        employee_id = int(emp_raw) if emp_raw else None
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid department/employee id.")

    # 3. Fetch Data (with Error Handling)
    try:
        context = get_salary_summary_data(request, month_str, department_id, employee_id)
    except PermissionError:
        return HttpResponseForbidden("আপনার কোম্পানি সেট করা নেই।")
    except Exception as e:
        return HttpResponseBadRequest(f"Data Generation Error: {str(e)}")

    # 4. Context Enhancements for PDF
    context["print_mode"] = True
    # PDF-এ ছবি দেখানোর জন্য Absolute URI প্রয়োজন
    context["logo_url"] = request.build_absolute_uri('/static/images/logo.png')
    context["generated_at"] = timezone.now()

    # 5. Render Template
    template = get_template('payroll/salary_summary_pdf.html')
    html_string = template.render(context)

    # 6. Generate PDF
    if 'HTML' not in globals():
        return HttpResponse("WeasyPrint library is missing.", status=500)

    pdf_io = BytesIO()
    # base_url is crucial for loading CSS/Images in PDF
    HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf(target=pdf_io)
    pdf_io.seek(0)

    filename = f"Salary_Summary_{month_str}.pdf"
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

