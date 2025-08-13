from django.shortcuts import render, redirect
from django.utils.timezone import make_aware, is_naive
from datetime import datetime, timedelta, date, time
from collections import defaultdict
from decimal import Decimal

from attendance_app.models import *
from .models import *
import os
from django.contrib.auth.decorators import login_required
from django.template.loader import get_template
from weasyprint import HTML
from django.http import HttpResponse
import tempfile
from django.contrib.auth.decorators import user_passes_test


from django.contrib.auth.decorators import user_passes_test

def is_not_attendance_group(user):
    return not user.groups.filter(name='attendance').exists()



# def generate_salary_for_month(request):
#     if request.method == 'POST':
#         month_str = request.POST.get('month')  # format: YYYY-MM
#         year, month = map(int, month_str.split('-'))
#         start_date = date(year, month, 1)
#         end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

#         employees = Employee.objects.select_related('department').all()

#         for emp in employees:
#             # Skip if salary not set
#             try:
#                 emp_salary = emp.employeesalary.base_salary
#             except EmployeeSalary.DoesNotExist:
#                 continue

#             attendances = Attendance.objects.filter(
#                 employee=emp,
#                 timestamp__date__range=(start_date, end_date)
#             ).order_by('timestamp')

#             approved_leaves = LeaveRequest.objects.filter(
#                 employee=emp,
#                 status='Approved',
#                 start_date__lte=end_date,
#                 end_date__gte=start_date
#             )

#             leave_dates = {
#                 leave.start_date + timedelta(days=i)
#                 for leave in approved_leaves
#                 for i in range((min(leave.end_date, end_date) - max(leave.start_date, start_date)).days + 1)
#             }

#             daily_attendance = defaultdict(list)
#             for att in attendances:
#                 daily_attendance[att.timestamp.date()].append(att)

#             off_day = emp.department.weekly_off_day if emp.department else None
#             expected_start_time = time(10, 30)
#             regular_work_time = timedelta(hours=10)

#             total_days = (end_date - start_date).days + 1
#             present_days = leave_days_count = weekly_off_count = 0
#             total_work_time = total_late_time = total_over_time = timedelta()

#             for n in range(total_days):
#                 current_date = start_date + timedelta(days=n)
#                 weekday = current_date.strftime('%A')

#                 if off_day == weekday:
#                     weekly_off_count += 1
#                     continue

#                 if current_date in leave_dates:
#                     leave_days_count += 1
#                     continue

#                 records = daily_attendance.get(current_date, [])
#                 in_times = [r.timestamp for r in records if r.status == 'In']
#                 out_times = [r.timestamp for r in records if r.status == 'Out']

#                 if in_times and out_times:
#                     in_time = min(in_times)
#                     out_time = max(out_times)

#                     if is_naive(in_time): in_time = make_aware(in_time)
#                     if is_naive(out_time): out_time = make_aware(out_time)

#                     adjusted_in_time = datetime.combine(in_time.date(), expected_start_time)
#                     if is_naive(adjusted_in_time): adjusted_in_time = make_aware(adjusted_in_time)

#                     actual_in_time = max(in_time, adjusted_in_time)

#                     if out_time > actual_in_time:
#                         duration = out_time - actual_in_time
#                         present_days += 1
#                         total_work_time += duration

#                         if in_time.time() > expected_start_time:
#                             late = datetime.combine(current_date, in_time.time()) - datetime.combine(current_date, expected_start_time)
#                             total_late_time += late

#                         if duration > regular_work_time:
#                             total_over_time += duration - regular_work_time

#             absent_days = total_days - present_days - leave_days_count - weekly_off_count
#             per_day_salary = emp_salary / Decimal(total_days)
#             earned_salary = per_day_salary * Decimal(present_days + leave_days_count + weekly_off_count)

#             # Save summary
#             SalarySummary.objects.update_or_create(
#                 employee=emp,
#                 month=month_str,
#                 defaults={
#                     'base_salary': emp_salary,
#                     'present_days': present_days,
#                     'absent_days': absent_days,
#                     'leave_days': leave_days_count,
#                     'weekly_off_days': weekly_off_count,
#                     'total_work_hours': total_work_time,
#                     'late_time': total_late_time,
#                     'early_leave_time': timedelta(),  # Placeholder
#                     'over_time': total_over_time,
#                     'final_salary': earned_salary
#                 }
#             )

#         return redirect('salary_summary_list')  # ✅ Make sure this URL name exists in urls.py

#     return render(request, 'payroll/generate_salary.html')

# payroll/views.py


# ... তোমার অন্য import গুলো

from collections import defaultdict
from decimal import Decimal
from datetime import datetime, timedelta, time
from django.utils.timezone import make_aware, is_naive
from django.contrib.auth.decorators import login_required, user_passes_test
from collections import defaultdict
from datetime import datetime, timedelta, time
from decimal import Decimal
from django.utils.timezone import is_naive, make_aware
from collections import defaultdict
from datetime import datetime, timedelta, time
from decimal import Decimal
from django.utils.timezone import is_naive, make_aware

def get_salary_summary_data(month_str, department_id=None, employee_id=None,user_company=None):
    summary_data = []
    departments = Department.objects.all()
    employees_qs = Employee.objects.select_related('department')

    # Safe filter for department and employee

    employees_qs = Employee.objects.select_related('department')
    if user_company:
        employees_qs = employees_qs.filter(company=user_company)
        departments = departments.filter(company=user_company)  # department list-ও company অনুযায়ী ফিল্টার হবে

    if department_id:
        try:
            department_id_int = int(department_id)
            employees_qs = employees_qs.filter(department__id=department_id_int)
        except (ValueError, TypeError):
            department_id_int = None
    else:
        department_id_int = None

    if employee_id:
        try:
            employee_id_int = int(employee_id)
            employees_qs = employees_qs.filter(id=employee_id_int)
        except (ValueError, TypeError):
            employee_id_int = None
    else:
        employee_id_int = None

    total_base_salary = Decimal(0)
    total_final_salary = Decimal(0)
    total_payable_cash = Decimal(0)

    # Handle month safely
    if month_str and month_str.lower() != 'none':
        try:
            year, month = map(int, month_str.split('-'))
        except ValueError:
            year, month = datetime.today().year, datetime.today().month
    else:
        year, month = datetime.today().year, datetime.today().month

    start_date = datetime(year, month, 1).date()
    end_date = (start_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    holidays = Holiday.objects.filter(start_date__lte=end_date, end_date__gte=start_date)
    public_holiday_dates = set()
    for holiday in holidays:
        overlap_start = max(start_date, holiday.start_date)
        overlap_end = min(end_date, holiday.end_date)
        for i in range((overlap_end - overlap_start).days + 1):
            public_holiday_dates.add(overlap_start + timedelta(days=i))

    for emp in employees_qs:
        try:
            salary = emp.employeesalary
            base_salary = salary.base_salary
            bank_transfer = salary.bank_transfer_amount
            cash = base_salary - bank_transfer
        except EmployeeSalary.DoesNotExist:
            continue

        attendances = Attendance.objects.filter(
            employee=emp,
            timestamp__date__range=(start_date, end_date)
        ).order_by('timestamp')

        approved_leaves = LeaveRequest.objects.filter(
            employee=emp,
            status='Approved',
            start_date__lte=end_date,
            end_date__gte=start_date
        )

        leave_dates = {
            leave.start_date + timedelta(days=i)
            for leave in approved_leaves
            for i in range((min(leave.end_date, end_date) - max(leave.start_date, start_date)).days + 1)
        }

        daily_attendance = defaultdict(list)
        for att in attendances:
            daily_attendance[att.timestamp.date()].append(att)

        off_day = emp.department.weekly_off_day if emp.department else None
        expected_start_time = emp.department.in_time if emp.department else time(10, 30)
        regular_work_time = timedelta(hours=10)

        total_days = (end_date - start_date).days + 1
        present_days = leave_days_count = weekly_off_count = public_holiday_count = 0
        total_work_time = total_late_time = total_over_time = timedelta()
        total_working_days = 0

        # মোট কাজের দিন (off_day এবং public holiday বাদে)
        for n in range(total_days):
            current_date = start_date + timedelta(days=n)
            weekday = current_date.strftime('%A')
            if weekday != off_day and current_date not in public_holiday_dates:
                total_working_days += 1

        for n in range(total_days):
            current_date = start_date + timedelta(days=n)
            weekday = current_date.strftime('%A')

            if current_date in public_holiday_dates:
                public_holiday_count += 1
                continue
            if weekday == off_day:
                weekly_off_count += 1
                continue

            records = daily_attendance.get(current_date, [])
            in_times = [r.timestamp for r in records if r.status == 'In']
            out_times = [r.timestamp for r in records if r.status == 'Out']

            if current_date in leave_dates:
                leave_days_count += 1
                if in_times:
                    in_time = min(in_times)
                    out_time = max(out_times) if out_times else None
                    if is_naive(in_time):
                        in_time = make_aware(in_time)
                    duration = timedelta()
                    if out_time:
                        if is_naive(out_time):
                            out_time = make_aware(out_time)
                        adjusted_in_time = datetime.combine(in_time.date(), expected_start_time)
                        if is_naive(adjusted_in_time):
                            adjusted_in_time = make_aware(adjusted_in_time)
                        actual_in_time = max(in_time, adjusted_in_time)
                        if out_time > actual_in_time:
                            duration = out_time - actual_in_time
                    total_work_time += duration
                    expected_datetime = datetime.combine(in_time.date(), expected_start_time)
                    if is_naive(expected_datetime):
                        expected_datetime = make_aware(expected_datetime)
                    if in_time > expected_datetime:
                        total_late_time += in_time - expected_datetime
                    if duration > regular_work_time:
                        total_over_time += duration - regular_work_time
                    present_days += 1
                else:
                    total_work_time += regular_work_time
                continue

            if in_times:
                in_time = min(in_times)
                out_time = max(out_times) if out_times else None
                if is_naive(in_time):
                    in_time = make_aware(in_time)
                duration = timedelta()
                if out_time:
                    if is_naive(out_time):
                        out_time = make_aware(out_time)
                    adjusted_in_time = datetime.combine(in_time.date(), expected_start_time)
                    if is_naive(adjusted_in_time):
                        adjusted_in_time = make_aware(adjusted_in_time)
                    actual_in_time = max(in_time, adjusted_in_time)
                    if out_time > actual_in_time:
                        duration = out_time - actual_in_time
                present_days += 1
                total_work_time += duration
                expected_datetime = datetime.combine(in_time.date(), expected_start_time)
                if is_naive(expected_datetime):
                    expected_datetime = make_aware(expected_datetime)
                if in_time > expected_datetime:
                    total_late_time += in_time - expected_datetime
                if duration > regular_work_time:
                    total_over_time += duration - regular_work_time

        absent_days = total_working_days - present_days - leave_days_count
        expected_work_hours = total_working_days * 10
        actual_work_hours = total_work_time.total_seconds() / 3600
        hourly_rate = base_salary / Decimal(expected_work_hours) if expected_work_hours > 0 else Decimal(0)

        if actual_work_hours < expected_work_hours:
            earned_salary = Decimal(actual_work_hours) * hourly_rate
        else:
            extra_hours = actual_work_hours - expected_work_hours
            earned_salary = (Decimal(expected_work_hours) * hourly_rate) + (Decimal(extra_hours) * hourly_rate * Decimal(1.5))

        payable_cash = earned_salary - bank_transfer

        total_base_salary += base_salary
        total_final_salary += earned_salary
        total_payable_cash += payable_cash

        total_seconds = int(total_work_time.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        formatted_total_work_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        diff_seconds = int((total_work_time - timedelta(hours=expected_work_hours)).total_seconds())
        diff_hours = abs(diff_seconds) // 3600
        diff_minutes = (abs(diff_seconds) % 3600) // 60
        diff_seconds_remain = abs(diff_seconds) % 60
        diff_sign = "-" if diff_seconds < 0 else "+"
        formatted_work_diff = f"{diff_sign}{diff_hours:02d}:{diff_minutes:02d}:{diff_seconds_remain:02d}"

        late_seconds = int(total_late_time.total_seconds())
        late_hours = late_seconds // 3600
        late_minutes = (late_seconds % 3600) // 60
        late_seconds_remain = late_seconds % 60
        formatted_late_time = f"{late_hours:02d}:{late_minutes:02d}:{late_seconds_remain:02d}"

        summary_data.append({
            'employee': emp,
            'month': f"{year}-{month:02d}",
            'base_salary': base_salary,
            'bank_transfer': round(bank_transfer, 2),
            'cash_amount': round(cash, 2),
            'present_days': present_days,
            'leave_days': leave_days_count,
            'absent_days': absent_days,
            'weekly_off_days': weekly_off_count,
            'holiday_days': public_holiday_count,
            'total_work_hours': formatted_total_work_time,
            'expected_work_hours': f"{expected_work_hours:.0f}:00:00",
            'work_time_difference': formatted_work_diff,
            'late_time': formatted_late_time,
            'over_time': total_over_time,
            'final_salary': round(earned_salary, 2),
            'payable_cash': round(payable_cash, 2),
        })

    total_salary_difference = total_final_salary - total_base_salary
    total_bank_transfer = sum([s.get('bank_transfer', Decimal(0)) for s in summary_data])
    total_cash_amount = sum([s.get('cash_amount', Decimal(0)) for s in summary_data])

    return {
        'summaries': summary_data,
        'departments': departments,
        'employees': Employee.objects.filter(department_id=department_id_int) if department_id_int else Employee.objects.all(),
        'selected_month': f"{year}-{month:02d}",
        'selected_department': department_id_int,
        'selected_employee': employee_id_int,
        'selected_department_id': department_id_int or '',
        'selected_employee_id': employee_id_int or '',
        'total_base_salary': round(total_base_salary, 2),
        'total_final_salary': round(total_final_salary, 2),
        'total_salary_difference': round(total_salary_difference, 2),
        'total_bank_transfer': round(total_bank_transfer, 2),
        'total_cash_amount': round(total_cash_amount, 2),
        'total_payable_cash': round(total_payable_cash, 2),
    }


@login_required
@user_passes_test(is_not_attendance_group)
def salary_summary_list(request):
    if request.user.groups.filter(name='attendance').exists():
        return redirect('dashboard')

    month_str = request.GET.get('month') or datetime.today().strftime('%Y-%m')
    department_id = request.GET.get('department') or ''
    employee_id = request.GET.get('employee') or ''

    # current user এর company বের করা
    user_company = getattr(request.user.profile, 'company', None)

    # company অনুযায়ী ডেটা আনা
    context = get_salary_summary_data(
        month_str,
        department_id,
        employee_id,
        user_company=user_company
    )
    return render(request, 'payroll/salary_summary_list.html', context)




@user_passes_test(is_not_attendance_group)
def export_salary_summary_pdf(request):
    month_str = request.GET.get('month') or datetime.today().strftime('%Y-%m')  # default current month
    department_id = request.GET.get('department')
    employee_id = request.GET.get('employee')

    # convert properly
    department_id = int(department_id) if department_id else None
    employee_id = int(employee_id) if employee_id else None

    # get context
    context = get_salary_summary_data(month_str, department_id, employee_id)

    template = get_template('payroll/salary_summary_pdf.html')
    html_string = template.render(context)

    html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))

    temp_path = os.path.join(tempfile.gettempdir(), "salary_summary_temp.pdf")
    html.write_pdf(temp_path)

    with open(temp_path, 'rb') as pdf_file:
        pdf = pdf_file.read()
    os.remove(temp_path)

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="salary_summary.pdf"'
    return response


# payroll/views.py
from .models import EmployeeSalary
from attendance_app.models import Employee
from django.shortcuts import render, redirect
from django.db import transaction
from decimal import Decimal, InvalidOperation


from django.db import transaction
from decimal import Decimal, InvalidOperation

@login_required
@user_passes_test(is_not_attendance_group)
def set_base_salaries(request):
    employees = Employee.objects.all()  # সকল employee

    if request.method == 'POST':
        with transaction.atomic():  # batch update
            for emp in employees:
                salary_val = request.POST.get(f'salary_{emp.id}')
                bank_val = request.POST.get(f'bank_transfer_{emp.id}')

                if salary_val:
                    try:
                        base_salary = Decimal(salary_val)
                    except InvalidOperation:
                        base_salary = Decimal(0)

                    try:
                        bank_transfer = Decimal(bank_val or 0)
                    except InvalidOperation:
                        bank_transfer = Decimal(0)

                    EmployeeSalary.objects.update_or_create(
                        employee=emp,
                        defaults={
                            'base_salary': base_salary,
                            'bank_transfer_amount': bank_transfer,
                        }
                    )
        return redirect('salary_summary_list')

    return render(request, 'payroll/set_base_salaries.html', {'employees': employees})
