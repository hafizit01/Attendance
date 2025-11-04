# Python built-ins
import json
import calendar
import logging
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, time, date
from io import BytesIO
# Django core
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Min, Max, Count
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden, FileResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import localtime, localdate, make_aware, is_naive, now
from django.views.decorators.csrf import csrf_exempt

# Third-party libs
from weasyprint import HTML,CSS
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.units import inch

# Local apps
from .forms import AttendanceForm, LeaveRequestForm, DepartmentForm, HolidayForm, EmployeeForm,DayAttendanceForm
from .models import Employee, Department, Attendance, Holiday, LeaveRequest
from attendance_app.utils.zk_import import import_attendance
from attendance_app.utils.attendance_helpers import generate_attendance_table
from subscription_app.models import UserSubscription
from subscription_app.decorators import subscription_required

from django.utils.safestring import mark_safe

# ---------- Subscription helper (Date/DateTime দুই কেস) ----------
def _has_active_subscription(user) -> bool:
    """
    ইউজারের সর্বশেষ সাবস্ক্রিপশন আজকের দিন-শেষ পর্যন্ত সক্রিয় কি না?
    DateField হলে দিন-শেষ (23:59:59.999999) পর্যন্ত বৈধ ধরা হয়।
    DateTimeField হলে tz-aware করে এখন (timezone.now) এর সাথে তুলনা হয়।
    """
    last = (
        UserSubscription.objects
        .filter(user=user)
        .order_by('-end_date')
        .first()
    )
    if not last or not last.end_date:
        return False

    end = last.end_date
    # DateTimeField?
    if isinstance(end, datetime):
        if timezone.is_naive(end):
            end = timezone.make_aware(end, timezone.get_current_timezone())
        return timezone.now() <= end

    # নাহলে DateField: দিন-শেষ পর্যন্ত বৈধ
    end_dt = timezone.make_aware(
        datetime.combine(end, time(23, 59, 59, 999999)),
        timezone.get_current_timezone()
    )
    return timezone.now() <= end_dt

# ---------- Dashboard (Optimized) ----------

@login_required
def dashboard(request):
    user = request.user

    # 1) সাবস্ক্রিপশন গার্ড (হার্ড ব্লক)
    if not _has_active_subscription(user):
        last_sub = (
            UserSubscription.objects
            .filter(user=user)
            .order_by('-end_date')
            .first()
        )
        return render(
            request,
            'subscription_app/expired.html',
            {'last_end_date': last_sub.end_date if last_sub else None}
        )

    # 2) কোম্পানি গার্ড
    user_company = getattr(getattr(user, 'profile', None), 'company', None)
    if not user_company:
        return render(request, 'dashboard.html', {
            'error_message': "আপনার প্রোফাইলে কোম্পানি সেট করা নেই।"
        })

    today = localdate()
    selected_dept = request.GET.get('department')

    # ---- (Q1) Employees + department eager load
    employees = (Employee.objects
                 .select_related('department')
                 .filter(department__company=user_company))
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)

    # ---- (Q2) Departments (filter dropdown)
    departments = Department.objects.filter(company=user_company)

    # IDs cache
    emp_ids = list(employees.values_list('id', flat=True))
    total_employees = len(emp_ids)

    # 3) আজকের অ্যাটেন্ডেন্স (one query for all employees)
    # ---- (Q3) bring all rows for today, then group in Python
    today_att_qs = (Attendance.objects
                    .filter(employee_id__in=emp_ids, timestamp__date=today)
                    .only('employee_id', 'timestamp', 'status')
                    .order_by('employee_id', 'timestamp'))

    # group rows by employee_id
    rows_by_emp = defaultdict(list)
    for att in today_att_qs:
        rows_by_emp[att.employee_id].append(att)

    employee_by_id = {e.id: e for e in employees}

    # per-employee compute (same logic as before, but in Python with grouped rows)
    employee_data = []
    regular_work_time = timedelta(hours=10)

    for emp_id, recs in rows_by_emp.items():
        emp = employee_by_id.get(emp_id)
        if not emp:
            continue

        in_times  = [r.timestamp for r in recs if r.status == 'In']
        out_times = [r.timestamp for r in recs if r.status == 'Out']

        total_work_time = timedelta()
        for i in range(min(len(in_times), len(out_times))):
            t_in, t_out = in_times[i], out_times[i]
            if is_naive(t_in):  t_in  = make_aware(t_in)
            if is_naive(t_out): t_out = make_aware(t_out)
            if t_out > t_in:
                total_work_time += (t_out - t_in)

        late_time = over_time = less_time = timedelta()
        first_in = localtime(in_times[0]) if in_times else None
        last_out = localtime(out_times[-1]) if out_times else None

        if first_in:
            # department.in_time থাকলে সেটাই, না থাকলে 10:30
            dept_start = getattr(getattr(emp, 'department', None), 'in_time', None)
            base_start = dept_start or time(10, 30)
            expected_start = first_in.replace(hour=base_start.hour, minute=base_start.minute, second=0, microsecond=0)
            if first_in > expected_start:
                late_time = first_in - expected_start

        if total_work_time > regular_work_time:
            over_time = total_work_time - regular_work_time
        elif total_work_time < regular_work_time:
            less_time = regular_work_time - total_work_time

        employee_data.append({
            'employee': emp,
            'in_time': first_in.time() if first_in else None,
            'out_time': last_out.time() if last_out else None,
            'total_work_time': total_work_time,
            'late_time': late_time,
            'over_time': over_time,
            'less_time': less_time,
        })

    # যারা আজকে কোনো রেকর্ড নেই—তারাও টেবিলে চাইলে দেখাতে পারো:
    for emp in employees:
        if emp.id not in rows_by_emp:
            employee_data.append({
                'employee': emp,
                'in_time': None,
                'out_time': None,
                'total_work_time': timedelta(),
                'late_time': timedelta(),
                'over_time': timedelta(),
                'less_time': timedelta(),
            })

    # 4) ৩০ দিনের ট্রেন্ড (one aggregate query)
    start_date = today - timedelta(days=29)

    # ---- (Q4) Aggregate per day: present (distinct employee with 'In'), late (In after 10:30)
    trend_agg = (
        Attendance.objects
        .filter(employee_id__in=emp_ids, timestamp__date__gte=start_date, timestamp__date__lte=today)
        .annotate(day=TruncDate('timestamp'))
        .values('day')
        .annotate(
            present=Count('employee', filter=Q(status='In'), distinct=True),
            late=Count('id', filter=Q(status='In', timestamp__time__gt='10:30:00'))
        )
        .order_by('day')
    )

    # map by date for O(1) lookup
    present_by_day = {row['day']: row['present'] for row in trend_agg}
    late_by_day    = {row['day']: row['late']    for row in trend_agg}

    attendance_trend = []
    for i in range(30):
        d = start_date + timedelta(days=i)
        p = present_by_day.get(d, 0)
        l = late_by_day.get(d, 0)
        a = total_employees - p
        attendance_trend.append({
            'date': d.strftime('%Y-%m-%d'),
            'present': p,
            'absent': a,
            'late': l
        })

    # 5) কন্টেক্সট
    context = {
        'employee_data': employee_data,
        'departments': departments,
        'selected_department': int(selected_dept) if selected_dept else None,
        'total_employees': total_employees,
        'present': sum(1 for e in employee_data if e['in_time']),
        'absent': sum(1 for e in employee_data if not e['in_time']),
        'late': sum(1 for e in employee_data if e['late_time'].total_seconds() > 0),
        'attendance_trend': attendance_trend,  # optional for debugging
        # safe JSON string for template JS (dates already formatted as strings in your attendance_trend)
        'attendance_trend_json': mark_safe(json.dumps(attendance_trend)),
        'can_view_salary': user.has_perm('payroll.view_salarysummary'),
    }
    return render(request, 'dashboard.html', context)


  # attendance_app/views.py

# 📌 ZKTeco Push API for Live Push
@csrf_exempt
def zkteco_push_view(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode('utf-8'))

            for entry in data:
                user_id = entry.get('uid')
                time_str = entry.get('time')
                timestamp = make_aware(datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S"))

                status = 'In' if timestamp.hour < 13 else 'Out'

                try:
                    emp = Employee.objects.get(device_user_id=user_id)
                    Attendance.objects.get_or_create(
                        employee=emp,
                        timestamp=timestamp,
                        status=status
                    )
                except Employee.DoesNotExist:
                    continue

            return JsonResponse({'status': 'success'})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})

    return JsonResponse({'status': 'invalid request'}, status=400)


# 📌 Manual Sync Button with Department Filter


logger = logging.getLogger(__name__)

@login_required
def sync_attendance_view(request):
    # ইউজারের প্রোফাইল থেকে কোম্পানি নাও
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই। সিস্টেম অ্যাডমিনের সাথে যোগাযোগ করুন।")
        return redirect('attendance_app:dashboard')

    # ওই কোম্পানির ডিপার্টমেন্টগুলো নাও
    departments = Department.objects.filter(company=user_company)

    if request.method == 'POST':
        department_id = request.POST.get('department_id')
        if not department_id:
            error_msg = "দয়া করে একটি ডিপার্টমেন্ট সিলেক্ট করুন।"
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': error_msg})
            messages.error(request, error_msg)
            return redirect('attendance_app:sync_attendance')

        try:
            department = departments.get(id=department_id)
        except Department.DoesNotExist:
            error_msg = "অবৈধ ডিপার্টমেন্ট সিলেক্ট করা হয়েছে।"
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': error_msg})
            messages.error(request, error_msg)
            return redirect('sync_attendance')

        # ডিপার্টমেন্ট মডেল থেকে IP ও Port নেওয়া
        if not department.device_ip or not department.device_port:
            error_msg = f"{department.name} ডিপার্টমেন্টের জন্য ডিভাইস IP/Port সেট করা নেই।"
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': error_msg})
            messages.error(request, error_msg)
            return redirect('sync_attendance')

        try:
            results = import_attendance([{
                'ip': department.device_ip,
                'port': department.device_port,
                'department': department
            }])

            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                messages_list = []
                for res in results:
                    messages_list.append({
                        'type': 'success' if res['status'] == 'success' else 'error',
                        'text': f"{res['department']}: {res['message']}"
                    })
                from django.http import JsonResponse
                return JsonResponse({'status': 'success', 'messages': messages_list})
            else:
                for res in results:
                    if res['status'] == 'success':
                        messages.success(request, f"{res['department']}: {res['message']}")
                    else:
                        messages.error(request, f"{res['department']}: {res['message']}")
        except Exception as e:
            logger.error(f"❌ Error syncing attendance for {department.name}: {e}")
            error_msg = f"❌ সিঙ্ক করার সময় সমস্যা হয়েছে: {e}"
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': error_msg})
            messages.error(request, error_msg)

        return redirect('employee_list')

    return render(request, 'sync_form.html', {'departments': departments})

# ✅ 4. Attendance Table Helper

def generate_attendance_table(employee_qs, start_date, end_date):
    days = []
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    delta = timedelta(days=1)

    while start <= end:
        for emp in employee_qs:
            day = start.date()
            is_holiday = day.strftime('%A') == emp.department.weekly_off_day
            attendance_qs = Attendance.objects.filter(employee=emp, timestamp__date=day)

            if attendance_qs.exists():
                in_time = attendance_qs.aggregate(Min('timestamp'))['timestamp__min']
                out_time = attendance_qs.aggregate(Max('timestamp'))['timestamp__max']
                status = "Present"
            elif is_holiday:
                in_time = out_time = None
                status = "Holiday"
            else:
                in_time = out_time = None
                status = "Absent"

            days.append({
                'employee': emp,
                'date': day,
                'in_time': in_time,
                'out_time': out_time,
                'status': status
            })
        start += delta

    return days
# ---------------Monthly Report (Company-scoped)---------------
@login_required
def monthly_work_time_report(request):
    # --- Company scope ---
    user = request.user
    profile = getattr(user, "profile", None)
    company = getattr(profile, "company", None)

    if not company:
        return HttpResponseForbidden("You don't have a company assigned. Please contact an administrator.")

    # --- Date range ---
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if not start_date_str or not end_date_str:
        today = timezone.localdate()
        start_date = today - timedelta(days=30)
        end_date = today
    else:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

    # --- Filters (department/employee) but always within user's company ---
    selected_dept = request.GET.get('department')
    selected_emp = request.GET.get('employee')

    employees = (
        Employee.objects
        .filter(company=company)
        .select_related('department', 'company')  # perf
    )

    if selected_dept:
        employees = employees.filter(department__id=selected_dept, department__company=company)

    if selected_emp:
        employees = employees.filter(id=selected_emp, company=company)

    # --- Holidays & Leaves are company-scoped ---
    holidays = Holiday.objects.filter(
        company=company,
        start_date__lte=end_date,
        end_date__gte=start_date
    )

    holiday_dates = {
        holiday.start_date + timedelta(days=i)
        for holiday in holidays
        for i in range((min(holiday.end_date, end_date) - max(holiday.start_date, start_date)).days + 1)
    }

    # ---- Helper: per-employee expected start & regular work time from Department ----
    from datetime import time as _time
    from django.utils.timezone import make_aware, is_naive  # ✅ ensure imported

    DEFAULT_IN = _time(10, 30)
    DEFAULT_OUT = _time(20, 30)

    def _dept_times(emp):
        """Return (expected_start_time: time, regular_work_time: timedelta) for an employee."""
        dep = getattr(emp, 'department', None)
        in_t = getattr(dep, 'in_time', None) if dep else None
        out_t = getattr(dep, 'out_time', None) if dep else None

        # Fallbacks
        in_t = in_t or DEFAULT_IN
        out_t = out_t or DEFAULT_OUT

        # Compute daily duration safely
        dt_in = datetime.combine(start_date, in_t)
        dt_out = datetime.combine(start_date, out_t)
        duration = dt_out - dt_in
        if duration.total_seconds() < 0:
            duration = timedelta(0)

        return in_t, duration

    report_data = []

    for emp in employees:
        expected_start_time, regular_work_time = _dept_times(emp)

        attendances = (
            Attendance.objects
            .filter(
                employee=emp,
                employee__company=company,
                timestamp__date__range=(start_date, end_date)
            )
            .order_by('timestamp')
        )

        approved_leaves = LeaveRequest.objects.filter(
            company=company,
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

        total_work_time = timedelta()
        total_late_time = timedelta()
        total_over_time = timedelta()
        present_days = 0
        leave_days_count = 0
        weekly_off_count = 0
        holiday_count = 0

        off_day = emp.department.weekly_off_day if emp.department else None
        total_days = (end_date - start_date).days + 1

        for n in range(total_days):
            current_date = start_date + timedelta(days=n)
            weekday = current_date.strftime('%A')

            if current_date in holiday_dates:
                holiday_count += 1
                continue

            if off_day == weekday:
                weekly_off_count += 1
                continue

            if current_date in leave_dates:
                leave_days_count += 1
                # Leave day হলে সম্পূর্ণ শিফট সময় কৃতিত্ব (Department-ভিত্তিক)
                total_work_time += regular_work_time
                continue

            records = daily_attendance.get(current_date, [])
            in_times = [r.timestamp for r in records if r.status == 'In']
            out_times = [r.timestamp for r in records if r.status == 'Out']

            # ✅ নতুন রুল: In বা Out – যেকোনো একটা থাকলে Present
            if in_times or out_times:
                present_days += 1

            if in_times:
                in_time_val = min(in_times)
            else:
                in_time_val = None

            out_time_val = max(out_times) if out_times else None

            # Duration ক্যালকুলেশন কেবল তখনই যখন দুটোই আছে
            if in_time_val and out_time_val:
                if is_naive(in_time_val):
                    in_time_val = make_aware(in_time_val)
                if is_naive(out_time_val):
                    out_time_val = make_aware(out_time_val)

                adjusted_in_dt = datetime.combine(in_time_val.date(), expected_start_time)
                if is_naive(adjusted_in_dt):
                    adjusted_in_dt = make_aware(adjusted_in_dt)

                actual_in_time = max(in_time_val, adjusted_in_dt)

                if out_time_val > actual_in_time:
                    duration = out_time_val - actual_in_time
                    total_work_time += duration

                    if in_time_val > adjusted_in_dt:
                        total_late_time += (in_time_val - adjusted_in_dt)

                    if duration > regular_work_time:
                        total_over_time += (duration - regular_work_time)

        expected_work_time = (present_days + leave_days_count) * regular_work_time
        total_less_time = max(expected_work_time - total_work_time, timedelta())
        early_leave_time = max(total_less_time - total_late_time, timedelta())

        absent_days = total_days - present_days - weekly_off_count - leave_days_count - holiday_count
        expected_work_time_excl_off = (total_days - weekly_off_count - holiday_count) * regular_work_time
        work_time_difference = expected_work_time_excl_off - total_work_time

        def format_timedelta(td):
            total_seconds = int(td.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        report_data.append({
            'employee': emp,
            'total_work_hours': format_timedelta(total_work_time),
            'late_time': format_timedelta(total_late_time),
            'less_time': format_timedelta(total_less_time),
            'early_leave_time': format_timedelta(early_leave_time),
            'over_time': format_timedelta(total_over_time),
            'final_work_time': format_timedelta(total_work_time),
            'present_days': present_days,
            'absent_days': absent_days,
            'weekly_off_days': weekly_off_count,
            'leave_days': leave_days_count,
            'holiday_days': holiday_count,
            'approved_leave_count': approved_leaves.count(),
            'start_date': start_date,
            'end_date': end_date,
            'expected_work_time_excl_off': expected_work_time_excl_off,  # raw timedelta—টেমপ্লেটে কাস্টম ফিল্টার দিলে H:M:S দেখাবে
            'work_time_difference': work_time_difference,                # raw timedelta—same
        })

    # Dropdown data: শুধুই নিজের কোম্পানির
    departments = Department.objects.filter(company=company)
    if selected_dept:
        employees_all = Employee.objects.filter(company=company, department__id=selected_dept)
    else:
        employees_all = Employee.objects.filter(company=company)

    context = {
        'report_data': report_data,
        'departments': departments,
        'employees': employees_all,
        'selected_department': int(selected_dept) if selected_dept else None,
        'selected_employee': int(selected_emp) if selected_emp else None,
        'start_date': start_date,
        'end_date': end_date,
    }

    return render(request, 'monthly_report.html', context)



# --------------Monthly Work Time PDF (Leave বাদ না দিয়ে Expected ঠিক রাখা)---------------


@login_required
def monthly_work_time_pdf(request):
    # --- Company scope ---
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        return HttpResponseForbidden("আপনার কোম্পানি সেট করা নেই।")

    # --- Date range (defaults: last 30 days) ---
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    today = timezone.localdate()
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else (today - timedelta(days=30))
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
    except ValueError:
        return HttpResponse("Invalid date format. Use YYYY-MM-DD", status=400)
    if end_date < start_date:
        return HttpResponse("end_date must be on/after start_date", status=400)

    # --- Filters (department/employee) within user's company ---
    selected_dept = request.GET.get('department')
    selected_emp = request.GET.get('employee')

    employees = (
        Employee.objects
        .filter(company=user_company)
        .select_related('department', 'company')
    )
    if selected_dept:
        employees = employees.filter(department__id=selected_dept, department__company=user_company)
    if selected_emp:
        employees = employees.filter(id=selected_emp, company=user_company)

    # --- Holidays (company-scoped, overlap with range) ---
    holidays = Holiday.objects.filter(
        company=user_company,
        start_date__lte=end_date,
        end_date__gte=start_date
    )
    holiday_dates = {
        hol.start_date + timedelta(days=i)
        for hol in holidays
        for i in range((min(hol.end_date, end_date) - max(hol.start_date, start_date)).days + 1)
    }

    # --- Constants ---
    regular_work_time = timedelta(hours=10)
    expected_start_time = time(10, 30)

    report_data = []

    for emp in employees:
        # Attendance
        attendances = (
            Attendance.objects
            .filter(
                employee=emp,
                employee__company=user_company,
                timestamp__date__range=(start_date, end_date)
            )
            .order_by('timestamp')
        )

        # Approved leaves
        approved_leaves = LeaveRequest.objects.filter(
            company=user_company,
            employee=emp,
            status='Approved',
            start_date__lte=end_date,
            end_date__gte=start_date
        )

        # Expand leave dates in range
        leave_dates = set()
        for leave in approved_leaves:
            leave_start = max(leave.start_date, start_date)
            leave_end = min(leave.end_date, end_date)
            for n in range((leave_end - leave_start).days + 1):
                leave_dates.add(leave_start + timedelta(days=n))

        # Group attendance by day
        daily_attendance = defaultdict(list)
        for att in attendances:
            daily_attendance[att.timestamp.date()].append(att)

        # Aggregates
        total_work_time = timedelta()
        total_late_time = timedelta()
        total_over_time = timedelta()
        total_less_time = timedelta()
        weekly_off_day = emp.department.weekly_off_day if emp.department else None

        days_in_range = (end_date - start_date).days + 1
        present_days = 0
        weekly_off_count = 0
        leave_day_count = 0
        holiday_count = 0

        for n in range(days_in_range):
            current_date = start_date + timedelta(days=n)
            weekday = current_date.strftime('%A')

            # Holiday -> exclude from expected
            if current_date in holiday_dates:
                holiday_count += 1
                continue

            # Weekly off -> exclude from expected
            if weekly_off_day and weekday == weekly_off_day:
                weekly_off_count += 1
                continue

            # Leave -> count as leave + credit 10h
            if current_date in leave_dates:
                leave_day_count += 1
                total_work_time += regular_work_time
                continue

            # Attendance
            records = daily_attendance.get(current_date, [])
            in_times = [r.timestamp for r in records if r.status == 'In']
            out_times = [r.timestamp for r in records if r.status == 'Out']

            if in_times:
                present_days += 1
                in_time = min(in_times)
                out_time = max(out_times) if out_times else None

                if is_naive(in_time):
                    in_time = make_aware(in_time)
                if out_time and is_naive(out_time):
                    out_time = make_aware(out_time)

                expected_start_dt = datetime.combine(current_date, expected_start_time)
                if is_naive(expected_start_dt):
                    expected_start_dt = make_aware(expected_start_dt)

                actual_in_time = max(in_time, expected_start_dt)

                if out_time and out_time > actual_in_time:
                    duration = out_time - actual_in_time
                    total_work_time += duration

                    if in_time > expected_start_dt:
                        total_late_time += in_time - expected_start_dt

                    if duration > regular_work_time:
                        total_over_time += duration - regular_work_time
                    elif duration < regular_work_time:
                        total_less_time += regular_work_time - duration

        # ✅ Expected Work Time: Weekly Off + Holiday বাদ, কিন্তু Leave বাদ হবে না
        expected_work_time = (days_in_range - weekly_off_count - holiday_count) * regular_work_time

        absent_days = days_in_range - present_days - weekly_off_count - leave_day_count - holiday_count
        work_time_difference = expected_work_time - total_work_time

        report_data.append({
            'employee': emp,
            'present_days': present_days,
            'absent_days': absent_days,
            'weekly_off_days': weekly_off_count,
            'leave_days': leave_day_count,
            'holiday_days': holiday_count,
            'total_work_hours': total_work_time,
            'expected_work_hours': expected_work_time,
            'difference_time': work_time_difference,
            'late_hours': total_late_time,
            'over_hours': total_over_time,
            'less_hours': total_less_time,
        })

    # Department name (validated in company)
    department_name = None
    if selected_dept:
        try:
            department = Department.objects.get(id=selected_dept, company=user_company)
            department_name = department.name
        except Department.DoesNotExist:
            department_name = None

    logo_url = request.build_absolute_uri('/static/images/logo.png')

    html_string = render_to_string('monthly_work_time_report_pdf.html', {
        'report_data': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'department_name': department_name,
        'logo_url': logo_url,
        'company_name': user_company.name,
    })

    pdf = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()

    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="monthly_work_time_{start_date}_to_{end_date}.pdf"'
    return response




# ------------emplyee add function------------

# Employee List View


@login_required
def employee_list(request):
    query = request.GET.get('q', '')  # ইউআরএল থেকে কুয়েরি নেয়া হচ্ছে

    # ইউজারের কোম্পানি বের করা
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই। সিস্টেম অ্যাডমিনের সাথে যোগাযোগ করুন।")
        return redirect('attendance_app:dashboard')

    # শুধুমাত্র ইউজারের কোম্পানির এমপ্লয়ি
    employees = Employee.objects.select_related('department').filter(department__company=user_company)

    # সার্চ ফিল্টার
    if query:
        employees = employees.filter(
            Q(name__icontains=query) |
            Q(device_user_id__icontains=query) |
            Q(id__icontains=query)
        )

    return render(request, 'employee_list.html', {
        'employees': employees,
        'query': query
    })
from django.contrib import messages

# Employee Add View
@login_required
def employee_add(request):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই। সিস্টেম অ্যাডমিনের সাথে যোগাযোগ করুন।")
        return redirect('employee_list')

    if request.method == 'POST':
        form = EmployeeForm(request.POST)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.department.company = user_company  # কোম্পানি সেট করা
            employee.save()
            messages.success(request, "Employee সফলভাবে যোগ হয়েছে।")
            return redirect('attendance_app:employee_list')
    else:
        form = EmployeeForm()

    return render(request, 'employee_form.html', {'form': form, 'title': 'Add Employee'})


# Employee Edit View
@login_required
def employee_edit(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    employee = get_object_or_404(Employee, pk=pk, department__company=user_company)

    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            form.save()
            messages.success(request, "Employee তথ্য আপডেট হয়েছে।")
            return redirect('attendance_app:employee_list')
    else:
        form = EmployeeForm(instance=employee)

    return render(request, 'employee_form.html', {'form': form, 'title': 'Edit Employee'})


# Employee Delete View
@login_required
def employee_delete(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    employee = get_object_or_404(Employee, pk=pk, department__company=user_company)

    if request.method == 'POST':
        employee.delete()
        messages.success(request, "Employee ডিলিট হয়েছে।")
        return redirect('attendance_app:employee_list')

    return render(request, 'employee_confirm_delete.html', {'employee': employee})

@login_required
def department_list(request):
    # User -> Profile -> Company
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect("dashboard")  # বা যেকোনো সেফ রুট

    # base queryset: শুধুই ইউজারের কোম্পানির ডিপার্টমেন্ট
    qs = (
        Department.objects
        .select_related("company")
        .filter(company=user_company)
        .order_by("id")
    )

    # search query (optional)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(company__name__icontains=q)  # চাইলে এটা বাদও দিতে পারো, যেহেতু company ফিক্সড
        )

    # pagination
    paginator = Paginator(qs, 10)  # প্রতি পেজে ১০টি
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "department_list.html", {
        "departments": page_obj.object_list,
        "page_obj": page_obj,
        "q": q,
    })

from django.core.paginator import Paginator
# ----------------------------
# Department Add/Edit (আগের মতোই company-guarded)
# ----------------------------
@login_required
def department_form_view(request, pk=None):
    user_company = getattr(getattr(request.user, "profile", None), "company", None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:department_list')

    if pk:
        dept = get_object_or_404(Department, pk=pk, company=user_company)
        title = 'Edit Department'
    else:
        dept = None
        title = 'Add Department'

    if request.method == 'POST':
        form = DepartmentForm(request.POST, instance=dept)
        if form.is_valid():
            department = form.save(commit=False)
            department.company = user_company  # user-এর কোম্পানি ফোর্স করা
            department.save()
            messages.success(request, f"Department {'updated' if pk else 'added'} successfully.")
            return redirect('attendance_app:department_list')
    else:
        form = DepartmentForm(instance=dept)

    return render(request, 'department_form.html', {
        'form': form,
        'title': title,
    })

# ----------------------------
# Department Delete
# ----------------------------
from django.views.decorators.http import require_POST
@login_required
@require_POST
def department_delete(request, pk):
    user_company = getattr(request.user.profile, "company", None)
    dept = get_object_or_404(Department, pk=pk, company=user_company)
    name = dept.name  # শুধু মেসেজে দেখানোর জন্য আগে রেখে দিলাম
    dept.delete()
    messages.success(request, f"Department '{name}' deleted successfully.")
    return redirect("attendance_app:department_list")

# ---------------------Attendance---------

# ----------------------------
# Weekday Mapping
# ----------------------------
WEEKDAY_MAP = {
    'Monday': 0,
    'Tuesday': 1,
    'Wednesday': 2,
    'Thursday': 3,
    'Friday': 4,
    'Saturday': 5,
    'Sunday': 6,
}
# ----------------------------
# Generate Attendance Table (updated)
# ----------------------------
from collections import defaultdict


def _daterange(d1, d2):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)

def _expand_date_range(start_d, end_d):
    """Start/End (date) থেকে সব মধ্যবর্তী date এর set বানায়"""
    return {d for d in _daterange(start_d, end_d)}

def _align_tz(dt: datetime, ref: datetime) -> datetime:
    """
    dt (naive) কে ref এর timezone-awareness এর সাথে মিলিয়ে দেয়।
    ref যদি aware হয়, dt কে aware বানায় (current timezone দিয়ে)।
    ref যদি naive হয়, dt naive-ই থাকে।
    """
    if timezone.is_aware(ref) and timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    if timezone.is_naive(ref) and timezone.is_aware(dt):
        return timezone.make_naive(dt, timezone.get_current_timezone())
    return dt

def generate_attendance_table(employees, start_date, end_date):
    summary = []

    # Parse dates
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()

    # Preload holidays within range -> set of dates
    holiday_qs = Holiday.objects.filter(start_date__lte=end, end_date__gte=start)
    holiday_dates = set()
    for h in holiday_qs:
        s = max(h.start_date, start)
        e = min(h.end_date, end)
        holiday_dates |= _expand_date_range(s, e)

    # Preload approved leaves within range -> per-employee set of dates
    leaves_qs = LeaveRequest.objects.filter(
        status='Approved',
        start_date__lte=end,
        end_date__gte=start
    ).select_related('employee')
    leaves_by_emp = defaultdict(set)
    for lv in leaves_qs:
        s = max(lv.start_date, start)
        e = min(lv.end_date, end)
        leaves_by_emp[lv.employee_id] |= _expand_date_range(s, e)

    # Weekly off map দরকার (তোমার কোডে WEEKDAY_MAP আছে ধরে নিলাম)
    # default: Friday -> 4 (Python weekday: Mon=0 ... Sun=6)
    for emp in employees.select_related('department'):
        department = getattr(emp, 'department', None)
        weekly_off_day_str = getattr(department, 'weekly_off_day', 'Friday')
        weekly_off_day = WEEKDAY_MAP.get(weekly_off_day_str, 4)

        # Pull all attendance for this emp within range in one go
        # NOTE: তোমার মডেল অনুযায়ী date ফিল্ড আলাদা না, timestamp__date দিয়ে ফিল্টার করছো
        emp_att_qs = (
            Attendance.objects
            .filter(employee=emp, timestamp__date__range=(start, end))
            .order_by('timestamp')
        )

        # Group by date
        daily_records = defaultdict(list)
        for rec in emp_att_qs:
            daily_records[rec.timestamp.date()].append(rec)

        # Standard in-time (10:30) — দিনে apply করতে হবে
        standard_in_time = time(10, 30)

        for cur in _daterange(start, end):
            records = daily_records.get(cur, [])
            in_time = None
            out_time = None
            attendance_id = None

            # Fast checks using precomputed sets
            is_public_holiday = cur in holiday_dates
            is_weekly_off = (cur.weekday() == weekly_off_day)
            is_on_leave = cur in leaves_by_emp.get(emp.id, set())

            if is_public_holiday:
                status = 'Public Holiday'
            elif is_weekly_off:
                status = 'Weekly Off'
            elif is_on_leave:
                status = 'Leave'
            elif records:
                ins = [r for r in records if r.status == 'In']
                outs = [r for r in records if r.status == 'Out']

                if ins:
                    earliest_in = min(ins, key=lambda r: r.timestamp)
                    attendance_id = earliest_in.id

                    # standard_in_datetime কে earliest_in.timestamp এর awareness অনুযায়ী align করো
                    std_in_dt = datetime.combine(cur, standard_in_time)
                    std_in_dt = _align_tz(std_in_dt, earliest_in.timestamp)

                    # লগিক: 10:30 এর আগে ইন করলে 10:30 ধরবো, নইলে actual
                    in_time = max(earliest_in.timestamp, std_in_dt)

                if outs:
                    latest_out = max(outs, key=lambda r: r.timestamp)
                    out_time = latest_out.timestamp

                status = 'Present' if (in_time or out_time) else 'Absent'
            else:
                status = 'Absent'

            # ইউনিফর্ম রো
            row = {
                'employee': emp,
                'date': cur,
                'in_time': in_time,
                'out_time': out_time,
                'status': status,
                'attendance_id': attendance_id,   # ✅ মূল key
                'id': attendance_id,              # ✅ backward-compat (যদি কোথাও 'id' রেফারেন্স থাকে)
                'editable': bool(attendance_id),  # টেমপ্লেটে কাজে লাগাতে পারো
            }
            summary.append(row)

    return summary


@login_required
def attendance_list(request):
    # ----------------------------
    # শুধু current user এর company এর employees দেখাবে
    # ----------------------------
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        employees = Employee.objects.none()  # কোন employee নেই
    else:
        employees = Employee.objects.filter(company=user_company).select_related('department')

    # Filter input
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    employee_id = request.GET.get('employee')
    department_id = request.GET.get('department')

    # Default: today's attendance
    if not start_date or not end_date:
        today = date.today()
        start_date = end_date = today.strftime('%Y-%m-%d')

    # Apply filters
    if employee_id:
        employees = employees.filter(id=employee_id)
    if department_id:
        employees = employees.filter(department__id=department_id)

    # Generate attendance summary
    attendance_summary = generate_attendance_table(employees, start_date, end_date)

    departments = Department.objects.filter(company=user_company) if user_company else Department.objects.none()

    return render(request, 'attendance_list.html', {
        'attendance_summary': attendance_summary,
        'employees': employees,
        'departments': departments,
        'request': request,
        'start_date': start_date,
        'end_date': end_date,
    })


@login_required
def attendance_list_pdf(request):
    """
    Attendance -> PDF (inline, no download).
    ডিজাইন/ফন্ট/স্টাইল সব টেমপ্লেটে থাকবে।
    """

    # Company scope
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        employees = Employee.objects.none()
    else:
        employees = Employee.objects.filter(company=user_company).select_related('department')

    # Filters
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    employee_id = request.GET.get('employee')
    department_id = request.GET.get('department')

    if not start_date or not end_date:
        today = date.today()
        start_date = end_date = today.strftime('%Y-%m-%d')

    if employee_id:
        employees = employees.filter(id=employee_id)
    if department_id:
        employees = employees.filter(department__id=department_id)

    # Raw data
    attendance_summary = generate_attendance_table(employees, start_date, end_date)

    # Helpers
    def _get(d, *keys):
        if isinstance(d, dict):
            for k in keys:
                if k in d and d[k] not in (None, ''):
                    return d[k]
        for k in keys:
            if hasattr(d, k):
                v = getattr(d, k)
                try:
                    v = v() if callable(v) else v
                except TypeError:
                    pass
                if v not in (None, ''):
                    return v
        return None

    def _fmt_time(t):
        if not t: return ''
        try:
            if isinstance(t, (datetime, time)): return t.strftime('%H:%M')
            return str(t)
        except Exception:
            return str(t)

    def _parse_hours(v):
        if v in (None, ''): return 0
        if isinstance(v, timedelta): return int(v.total_seconds())
        if isinstance(v, (int, float)): return int(float(v) * 3600)
        s = str(v).strip()
        if ':' in s:
            try:
                h, m = s.split(':', 1); return int(h)*3600 + int(m)*60
            except Exception: pass
        try:
            return int(float(s) * 3600)
        except Exception:
            return 0

    # Normalize rows + total
    rows, total_seconds = [], 0
    for r in attendance_summary:
        src = r if isinstance(r, dict) else getattr(r, '__dict__', {})

        emp  = _get(src, 'employee', 'emp', 'user', 'staff')
        dept = _get(src, 'department') or (getattr(emp, 'department', None) if emp else None)

        employee_name = (
            _get(src, 'employee_name', 'emp_name')
            or (_get(emp, 'get_full_name') if emp else None)
            or _get(emp, 'full_name', 'name', 'username')
            or (f"{getattr(getattr(emp, 'user', None), 'first_name', '')} {getattr(getattr(emp, 'user', None), 'last_name', '')}".strip() if hasattr(emp, 'user') else None)
            or (getattr(getattr(emp, 'user', None), 'username', None) if hasattr(emp, 'user') else None)
            or (str(emp) if emp else '')
        )

        department_name = (
            _get(src, 'department_name') or
            _get(dept, 'name') or
            _get(src, 'dept_name') or
            ''
        )

        check_in  = _get(src, 'check_in', 'in_time', 'check_in_time', 'clock_in')
        check_out = _get(src, 'check_out', 'out_time', 'check_out_time', 'clock_out')
        worked    = _get(src, 'worked_hours', 'work_hours', 'total_hours', 'duration')
        status    = _get(src, 'status', 'attendance_status', 'state')
        adate     = _get(src, 'date', 'attendance_date', 'day')

        worked_seconds = _parse_hours(worked)
        if worked_seconds == 0 and check_in and check_out:
            try:
                if isinstance(check_in, datetime) and isinstance(check_out, datetime):
                    worked_seconds = int((check_out - check_in).total_seconds())
                elif isinstance(check_in, time) and isinstance(check_out, time):
                    from datetime import datetime as dt
                    dt0 = dt.combine(date.today(), check_in)
                    dt1 = dt.combine(date.today(), check_out)
                    worked_seconds = int((dt1 - dt0).total_seconds())
                else:
                    worked_seconds = _parse_hours(_fmt_time(check_out)) - _parse_hours(_fmt_time(check_in))
                    if worked_seconds < 0:
                        worked_seconds += 24*3600
            except Exception:
                worked_seconds = 0

        total_seconds += max(0, worked_seconds)
        wh = worked_seconds
        worked_hhmm = f"{wh // 3600:02d}:{(wh % 3600)//60:02d}" if wh > 0 else (_fmt_time(worked) or '—')

        rows.append({
            'employee_name': (employee_name or '—'),
            'department_name': (department_name or '—'),
            'date': adate or '—',
            'check_in': _fmt_time(check_in) or '—',
            'check_out': _fmt_time(check_out) or '—',
            'worked_hours': worked_hhmm,
            'status': status or '—',
        })

    total_h = total_seconds // 3600
    total_m = (total_seconds % 3600) // 60
    total_worked = f"{total_h:02d}:{total_m:02d}"

    # Render HTML (সব ডিজাইন টেমপ্লেটে)
    html_string = render_to_string('attendance_list_pdf.html', {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'user': request.user,
        'total_worked': total_worked,
    })

    # Build PDF (টেমপ্লেটের CSS/Google Fonts-ই ইউজ হবে)
    pdf_io = BytesIO()
    HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf(target=pdf_io)
    pdf_io.seek(0)

    # Inline view
    resp = FileResponse(pdf_io, content_type='application/pdf', as_attachment=False)
    resp['Content-Disposition'] = f'inline; filename="attendance_{start_date}_to_{end_date}.pdf"'
    return resp



@login_required
def attendance_add(request):
    user_company = getattr(request.user.profile, 'company', None)
    
    if request.method == 'POST':
        form = AttendanceForm(request.POST)
        if form.is_valid():
            attendance = form.save(commit=False)
            attendance.user = request.user
            attendance.save()
            return redirect('attendance_app:attendance_list')
    else:
        form = AttendanceForm()
        # শুধু সেই কোম্পানির employees dropdown এ দেখাবে
        if user_company:
            form.fields['employee'].queryset = Employee.objects.filter(company=user_company)
        else:
            form.fields['employee'].queryset = Employee.objects.none()

    return render(request, 'attendance_form.html', {
        'form': form,
        'title': 'Add Attendance'
    })



from django.utils.timezone import is_aware, make_naive, get_current_timezone

def to_dt_local_str(dt):
    """HTML datetime-local এর জন্য YYYY-MM-DDTHH:MM স্ট্রিং বানায়"""
    if not dt:
        return ""
    # aware হলে naive এ রূপান্তর (current timezone)
    if is_aware(dt):
        dt = make_naive(dt, get_current_timezone())
    return dt.strftime('%Y-%m-%dT%H:%M')

@login_required
def attendance_edit(request, pk):
    user_company = getattr(getattr(request.user, 'profile', None), 'company', None)

    # anchor record (company scope)
    anchor = get_object_or_404(
        Attendance.objects.select_related('employee'),
        pk=pk,
        employee__company=user_company
    )
    emp = anchor.employee
    day = anchor.timestamp.date()

    # ওই দিনের সব রেকর্ড
    day_qs = Attendance.objects.filter(employee=emp, timestamp__date=day).order_by('timestamp')
    ins  = [r for r in day_qs if r.status == 'In']
    outs = [r for r in day_qs if r.status == 'Out']
    earliest_in  = min(ins, key=lambda r: r.timestamp) if ins else None
    latest_out   = max(outs, key=lambda r: r.timestamp) if outs else None

    if request.method == 'POST':
        form = DayAttendanceForm(request.POST)
        if form.is_valid():
            in_time  = form.cleaned_data['in_time']
            out_time = form.cleaned_data['out_time']

            # anchor.timestamp-এর aware/naive অনুযায়ী align
            def align(dt):
                if dt is None:
                    return None
                if is_aware(anchor.timestamp):
                    return dt if is_aware(dt) else make_aware(dt, get_current_timezone())
                else:
                    return dt if not is_aware(dt) else make_naive(dt, get_current_timezone())

            in_time  = align(in_time)
            out_time = align(out_time)

            # In update/create/delete
            if in_time:
                if earliest_in:
                    earliest_in.timestamp = in_time
                    earliest_in.save(update_fields=['timestamp'])
                else:
                    Attendance.objects.create(
                        employee=emp, timestamp=in_time, status='In',
                        company=getattr(emp, 'company', None)  # থাকলে
                    )
            else:
                if earliest_in:
                    earliest_in.delete()

            # Out update/create/delete
            if out_time:
                if latest_out:
                    latest_out.timestamp = out_time
                    latest_out.save(update_fields=['timestamp'])
                else:
                    Attendance.objects.create(
                        employee=emp, timestamp=out_time, status='Out',
                        company=getattr(emp, 'company', None)  # থাকলে
                    )
            else:
                if latest_out:
                    latest_out.delete()

            return redirect('attendance_app:attendance_list')
    else:
        # ❗️HTML datetime-local এর জন্য স্ট্রিং বানিয়ে টেমপ্লেটে পাঠাই
        in_value  = to_dt_local_str(earliest_in.timestamp if earliest_in else None)
        out_value = to_dt_local_str(latest_out.timestamp if latest_out else None)
        form = DayAttendanceForm(initial={
            # initial রাখলেও আমরা ম্যানুয়াল ইনপুটে value বসাব
            'in_time':  in_value,
            'out_time': out_value,
        })

    return render(request, 'attendance_edit_form.html', {
        'form': form,
        'title': f'Edit Attendance for {emp} on {day}',
        'in_value': in_value if request.method == 'GET' else "",
        'out_value': out_value if request.method == 'GET' else "",
    })
# ----------------------------
# Delete Attendance
# ----------------------------
@login_required
def attendance_delete(request, pk):
    user_company = getattr(getattr(request.user, 'profile', None), 'company', None)

    # pk থেকে দিন ও employee বের করি
    anchor = get_object_or_404(
        Attendance.objects.select_related('employee'),
        pk=pk,
        employee__company=user_company
    )
    day = anchor.timestamp.date()

    if request.method == 'POST':
        # ✅ সেই দিনের একই employee-র সব In/Out রেকর্ড ডিলিট
        Attendance.objects.filter(
            employee=anchor.employee,
            timestamp__date=day
        ).delete()
        return redirect('attendance_app:attendance_list')

    return render(request, 'attendance_confirm_delete.html', {'attendance': anchor})

@login_required
def employee_attendance_detail(request, employee_id):
    # Employee নাও
    employee = get_object_or_404(Employee.objects.select_related('department'), id=employee_id)

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    # Default: current month's 1st day to today
    if not (start_date and end_date):
        today = datetime.today().date()
        start_date = today.replace(day=1)
        end_date = today

    # Convert to date if received as string
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()

    weekly_off_day = employee.department.weekly_off_day

    # Prepare date range summary with default values
    summary = OrderedDict()
    day_count = (end_date - start_date).days + 1
    for i in range(day_count):
        current_date = start_date + timedelta(days=i)
        weekday_name = calendar.day_name[current_date.weekday()]
        summary[current_date] = {
            'in_time': None,
            'out_time': None,
            'status': 'Holiday' if weekday_name == weekly_off_day else 'Absent'
        }

    # ------------------------------
    # Public Holidays: Current User Company Only
    # ------------------------------
    user_company = getattr(request.user.profile, 'company', None)
    public_holidays = Holiday.objects.filter(
        end_date__gte=start_date,
        start_date__lte=end_date,
    )
    if user_company:
        public_holidays = public_holidays.filter(company=user_company)
    else:
        public_holidays = Holiday.objects.none()

    public_holiday_dates = set()
    for holiday in public_holidays:
        holiday_start = max(holiday.start_date, start_date)
        holiday_end = min(holiday.end_date, end_date)
        for i in range((holiday_end - holiday_start).days + 1):
            public_holiday_dates.add(holiday_start + timedelta(days=i))

    for ph_date in public_holiday_dates:
        if ph_date in summary and summary[ph_date]['status'] == 'Absent':
            summary[ph_date]['status'] = 'Public Holiday'

    # ------------------------------
    # Attendance Records
    # ------------------------------
    attendances = Attendance.objects.filter(
        employee=employee,
        timestamp__date__range=(start_date, end_date)
    ).order_by('timestamp')

    daily_attendance = defaultdict(list)
    for att in attendances:
        att_date = att.timestamp.date()
        if att_date in summary:
            daily_attendance[att_date].append(att.timestamp)

    from datetime import time as _time
    threshold_time = _time(14, 0)  # 14:00 cutoff

    for att_date, timestamps in daily_attendance.items():
        if not timestamps:
            continue

        timestamps.sort()

        # 14:00-এর আগে/পরে ভাগ করো
        before_14 = [ts for ts in timestamps if ts.time() < threshold_time]
        after_14 = [ts for ts in timestamps if ts.time() >= threshold_time]

        in_time = None
        out_time = None

        if before_14:
            # আগে পাঞ্চ থাকলে earliest = in_time
            in_time = before_14[0]
            # out_time: preference after_14 last > before_14 last (if multiple)
            if after_14:
                out_time = after_14[-1]
            elif len(before_14) > 1:
                out_time = before_14[-1]
        else:
            # আগে কোনো পাঞ্চ নেই (সব 14:00-এর পরে)
            if len(after_14) == 1:
                # একটাই পাঞ্চ: শুধু out_time হবে
                out_time = after_14[0]
            else:
                # একাধিক পাঞ্চ: প্রথমটা in_time, শেষটা out_time
                in_time = after_14[0]
                out_time = after_14[-1]

        summary[att_date]['in_time'] = in_time
        summary[att_date]['out_time'] = out_time

        # ✅ in_time বা out_time যেকোনো একটা থাকলেই Present
        if in_time or out_time:
            summary[att_date]['status'] = 'Present'

    # ------------------------------
    # Approved Leaves
    # ------------------------------
    approved_leaves = LeaveRequest.objects.filter(
        employee=employee,
        status='Approved',
        start_date__lte=end_date,
        end_date__gte=start_date,
    )

    leave_dates = set()
    for leave in approved_leaves:
        current = max(leave.start_date, start_date)
        last = min(leave.end_date, end_date)
        for i in range((last - current).days + 1):
            leave_dates.add(current + timedelta(days=i))

    for leave_date in leave_dates:
        if leave_date in summary:
            summary[leave_date]['status'] = 'Leave'
            summary[leave_date]['in_time'] = None
            summary[leave_date]['out_time'] = None

    # ------------------------------
    # Count Stats
    # ------------------------------
    approved_leave_count = len([d for d in summary if summary[d]['status'] == 'Leave'])
    absent_days = len([d for d in summary if summary[d]['status'] == 'Absent'])
    public_holiday_count = len([d for d in summary if summary[d]['status'] == 'Public Holiday'])
    weekly_holiday_count = len([d for d in summary if summary[d]['status'] == 'Holiday'])
    total_leave_requests = LeaveRequest.objects.filter(
        employee=employee,
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).count()

    # ------------------------------
    # Calculate Work Duration
    # ------------------------------
    total_work_duration = timedelta()

    # Department-based shift
    DEFAULT_IN = _time(10, 30)
    DEFAULT_OUT = _time(20, 30)
    dept = getattr(employee, "department", None)

    dept_in = getattr(dept, "in_time", None) or DEFAULT_IN
    dept_out = getattr(dept, "out_time", None) or DEFAULT_OUT

    shift_start_dt = datetime.combine(start_date, dept_in)
    shift_end_dt = datetime.combine(start_date, dept_out)
    shift_duration = shift_end_dt - shift_start_dt
    if shift_duration.total_seconds() < 0:
        shift_duration = timedelta(0)

    expected_start_time = dept_in

    for date_key, data in summary.items():
        if data['status'] == 'Present' and data['in_time'] and data['out_time']:
            in_time = data['in_time']
            out_time = data['out_time']

            if is_naive(in_time):
                in_time = make_aware(in_time)
            if is_naive(out_time):
                out_time = make_aware(out_time)

            adjusted_in_time = datetime.combine(in_time.date(), expected_start_time)
            if is_naive(adjusted_in_time):
                adjusted_in_time = make_aware(adjusted_in_time)

            if in_time < adjusted_in_time:
                in_time = adjusted_in_time

            if out_time > in_time:
                duration = out_time - in_time
                total_work_duration += duration

        elif data['status'] == 'Leave':
            # Leave দিনে শিফটের সময় ক্রেডিট হবে
            total_work_duration += shift_duration

    context = {
        'employee': employee,
        'attendance_summary': summary.items(),
        'start_date': start_date.strftime("%Y-%m-%d"),
        'end_date': end_date.strftime("%Y-%m-%d"),
        'total_work_duration': total_work_duration,
        'approved_leave_count': approved_leave_count,
        'absent_days': absent_days,
        'public_holiday_count': public_holiday_count,
        'weekly_holiday_count': weekly_holiday_count,
        'total_leave_requests': total_leave_requests,
    }

    return render(request, 'employee_attendance_detail.html', context)



# ----------------------attendance details pdf-------------

from datetime import timedelta

def format_timedelta(td):
    if not isinstance(td, timedelta):
        return td
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

@login_required
def employee_attendance_pdf(request, employee_id):
    emp = get_object_or_404(Employee, id=employee_id)

    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    today = datetime.today().date()
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else today - timedelta(days=30)
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else today

    attendance_qs = Attendance.objects.filter(
        employee=emp,
        timestamp__date__range=(start_date, end_date)
    ).order_by('timestamp')

    daily_logs = defaultdict(list)
    for att in attendance_qs:
        daily_logs[att.timestamp.date()].append(att)

    off_day = emp.department.weekly_off_day if emp.department else None

    attendance_summary = {}
    total_work_duration = timedelta()
    total_over_time = timedelta()
    total_less_time = timedelta()
    holiday_count = 0
    absent_count = 0
    standard_work_duration = timedelta(hours=10)
    expected_start_time = time(10, 30)

    for day in (start_date + timedelta(days=n) for n in range((end_date - start_date).days + 1)):
        weekday_name = day.strftime('%A')
        logs = daily_logs.get(day, [])
        in_times = [log.timestamp for log in logs if log.status == 'In']
        out_times = [log.timestamp for log in logs if log.status == 'Out']

        if off_day and weekday_name == off_day:
            status = "Off Day"
            holiday_count += 1
            in_time = None
            out_time = None
            daily_work_time = timedelta()
            over_time = timedelta()
            less_time = timedelta()
        elif in_times:
            in_time = localtime(min(in_times))
            out_time = localtime(max(out_times)) if out_times else None

            adjusted_start_time = make_aware(datetime.combine(day, expected_start_time))
            actual_in_time = max(in_time, adjusted_start_time)

            if out_time and out_time > actual_in_time:
                daily_work_time = out_time - actual_in_time
            else:
                daily_work_time = timedelta()

            total_work_duration += daily_work_time
            status = "Present"

            # Compare with standard_work_duration
            if daily_work_time > standard_work_duration:
                over_time = daily_work_time - standard_work_duration
                less_time = timedelta()
            else:
                less_time = standard_work_duration - daily_work_time
                over_time = timedelta()

            total_over_time += over_time
            total_less_time += less_time
        else:
            in_time = None
            out_time = None
            daily_work_time = timedelta()
            over_time = timedelta()
            less_time = timedelta()
            status = "Absent"
            absent_count += 1

        attendance_summary[day] = {
            'in_time': actual_in_time if in_times else None,
            'out_time': out_time,
            'status': status,
            'weekday': weekday_name,
            'daily_work_time': daily_work_time,
            'over_time': over_time,
            'less_time': less_time
        }

    # Approved Leaves
    approved_leaves = LeaveRequest.objects.filter(
        employee=emp,
        status='Approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    )

    approved_leave_days = set()
    for leave in approved_leaves:
        current = max(leave.start_date, start_date)
        last = min(leave.end_date, end_date)
        while current <= last:
            approved_leave_days.add(current)
            current += timedelta(days=1)

    for leave_day in approved_leave_days:
        if leave_day in attendance_summary:
            current_status = attendance_summary[leave_day]['status']
            if current_status != 'Present':
                attendance_summary[leave_day]['status'] = 'On Leave'
                attendance_summary[leave_day]['in_time'] = None
                attendance_summary[leave_day]['out_time'] = None
                attendance_summary[leave_day]['daily_work_time'] = standard_work_duration
                attendance_summary[leave_day]['over_time'] = timedelta()
                attendance_summary[leave_day]['less_time'] = timedelta()
                if current_status == 'Absent':
                    absent_count -= 1
                if current_status == 'Off Day':
                    holiday_count -= 1

    approved_leave_count = len(approved_leave_days)
    total_leave_requests = LeaveRequest.objects.filter(
        employee=emp,
        start_date__lte=end_date,
        end_date__gte=start_date
    ).count()

    # Format times for display
    formatted_attendance_summary = []
    for date, data in attendance_summary.items():
        formatted_attendance_summary.append((date, {
            'weekday': data['weekday'],
            'status': data['status'],
            'in_time': data['in_time'].strftime('%I:%M %p') if data['in_time'] else None,
            'out_time': data['out_time'].strftime('%I:%M %p') if data['out_time'] else None,
            'daily_work_time': str(data['daily_work_time']),
            'over_time': str(data['over_time']),
            'less_time': str(data['less_time'])
        }))

    context = {
        'employee': emp,
        'start_date': start_date,
        'end_date': end_date,
        'attendance_summary': formatted_attendance_summary,
        'approved_leave_count': approved_leave_count,
        'holiday_count': holiday_count,
        'absent_count': absent_count,
        'total_leave_requests': total_leave_requests,
        'total_work_duration': total_work_duration,
        'total_over_time': total_over_time,
        'total_less_time': total_less_time,
    }

    html_string = render_to_string('details_pdf_template.html', context)
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="attendance_{emp.name}_{start_date}_to_{end_date}.pdf"'
    return response



# ----------attendance_pdf_report-------------

@login_required
def attendance_pdf_report(request, employee_id):
    from .models import Attendance, Employee  # তোমার মডেল অনুযায়ী adjust করো

    employee = Employee.objects.get(pk=employee_id)

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    attendance_qs = Attendance.objects.filter(employee=employee)
    if start_date:
        attendance_qs = attendance_qs.filter(timestamp__date__gte=start_date)
    if end_date:
        attendance_qs = attendance_qs.filter(timestamp__date__lte=end_date)
    attendance_qs = attendance_qs.order_by('timestamp')

    # Prepare response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="attendance_{employee.name}.pdf"'

    p = canvas.Canvas(response, pagesize=letter)
    width, height = letter

    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, height - 50, f"Attendance Report for {employee.name}")

    p.setFont("Helvetica", 12)
    y = height - 80
    p.drawString(50, y, "Date")
    p.drawString(150, y, "In Time")
    p.drawString(250, y, "Out Time")
    p.drawString(350, y, "Status")

    y -= 20

    # Group attendance by date and summarize In and Out times (same logic as your summary)
    attendance_by_date = {}
    for att in attendance_qs:
        date_key = att.timestamp.date()
        if date_key not in attendance_by_date:
            attendance_by_date[date_key] = {'in_time': None, 'out_time': None, 'status': 'Absent'}
        # You may adjust logic to set in_time and out_time properly:
        if att.status.lower() == 'in':
            if attendance_by_date[date_key]['in_time'] is None or att.timestamp < attendance_by_date[date_key]['in_time']:
                attendance_by_date[date_key]['in_time'] = att.timestamp
        elif att.status.lower() == 'out':
            if attendance_by_date[date_key]['out_time'] is None or att.timestamp > attendance_by_date[date_key]['out_time']:
                attendance_by_date[date_key]['out_time'] = att.timestamp
        attendance_by_date[date_key]['status'] = 'Present'

    for date, data in sorted(attendance_by_date.items()):
        if y < 50:
            p.showPage()
            y = height - 50
        p.drawString(50, y, date.strftime("%Y-%m-%d"))
        in_time_str = data['in_time'].astimezone().strftime("%I:%M %p") if data['in_time'] else "-"
        out_time_str = data['out_time'].astimezone().strftime("%I:%M %p") if data['out_time'] else "-"
        p.drawString(150, y, in_time_str)
        p.drawString(250, y, out_time_str)
        p.drawString(350, y, data['status'])
        y -= 20

    p.showPage()
    p.save()
    return response



# --------------Leave Request--------


@login_required
def leave_list(request):
    leaves = LeaveRequest.objects.select_related('employee')

    query = request.GET.get('q', '')
    if query:
        leaves = leaves.filter(employee__name__icontains=query)

    context = {
        'leaves': leaves,
        'query': query
    }

    # যদি HTMX request হয়, শুধুমাত্র table অংশ রেন্ডার করো
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'partials/leave_table.html', context)

    return render(request, 'leave_list.html', context)




# views.py


# Optional PDF library (WeasyPrint). না থাকলে HTML fallback হবে।
try:
    from weasyprint import HTML
except ImportError:
    HTML = None


def _clip_days(start, end, d_from, d_to):
    """
    Overlap window ধরে inclusive দিন গণনা:
    - যদি filter date_from/date_to দেয়া থাকে, leave span ক্লিপ করি
    - ক্লিপড start > end হলে 0 দিন
    """
    if d_from:
        start = max(start, d_from)
    if d_to:
        end = min(end, d_to)
    if end < start:
        return 0
    return (end - start).days + 1


@login_required
def leave_summary(request):
    """
    UI view: ফিল্টারসহ per-employee leave দিন দেখাবে (start/end overlap ধরে)
    Filters:
      - q: employee name / remarks
      - status: Approved/Pending/Rejected
      - department: department id
      - date_from, date_to: overlap window
    """
    # --- Filters ---
    q        = (request.GET.get('q') or '').strip()
    status   = (request.GET.get('status') or '').strip()
    dept_id  = (request.GET.get('department') or '').strip()
    d_from   = parse_date(request.GET.get('date_from') or '')
    d_to     = parse_date(request.GET.get('date_to') or '')

    # --- Base queryset ---
    qs = (LeaveRequest.objects
          .select_related('employee', 'employee__department')
          .only('employee__id', 'employee__name', 'employee__department__name',
                'start_date', 'end_date', 'status', 'leave_type', 'remarks')
          .order_by('-start_date', '-id'))

    # --- Apply filters ---
    if q:
        qs = qs.filter(Q(employee__name__icontains=q) | Q(remarks__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if dept_id:
        qs = qs.filter(employee__department_id=dept_id)
    # overlap window filter
    if d_from:
        qs = qs.filter(end_date__gte=d_from)
    if d_to:
        qs = qs.filter(start_date__lte=d_to)

    # --- Aggregate per employee (by days, not requests) ---
    bucket = {}  # emp_id -> dict
    rows = qs.values('employee_id', 'employee__name', 'start_date', 'end_date', 'leave_type')

    for r in rows:
        emp_id = r['employee_id']
        if emp_id not in bucket:
            bucket[emp_id] = {
                'employee': r['employee__name'],
                'total_days': 0,          # মোট leave days (overlap ধরে)
                'requests': 0,            # কতগুলো request মিলেছে (info purpose)
                'type_days': defaultdict(int),  # টাইপ অনুযায়ী দিনের যোগফল
            }
        days = _clip_days(r['start_date'], r['end_date'], d_from, d_to)
        if days <= 0:
            continue
        bucket[emp_id]['total_days'] += days
        bucket[emp_id]['requests'] += 1
        if r['leave_type']:
            bucket[emp_id]['type_days'][r['leave_type']] += days

    # --- List + sort ---
    summary = []
    for it in bucket.values():
        summary.append({
            'employee': it['employee'],
            'total_days': it['total_days'],
            'requests': it['requests'],
            'type_breakdown': ', '.join(f"{k}:{v}" for k, v in sorted(it['type_days'].items())),
        })
    summary.sort(key=lambda x: (x['employee'] or '').lower())

    context = {
        'summary': summary,
        'q': q, 'status': status, 'department': dept_id,
        'date_from': d_from, 'date_to': d_to,
        'departments': Department.objects.only('id', 'name').order_by('name'),
    }
    return render(request, 'leave_summary.html', context)


@login_required
def leave_summary_pdf(request):
    """
    PDF view: UI-র মতই একই ফিল্টার + একই দিনের হিসাব।
    WeasyPrint থাকলে PDF, না থাকলে HTML fallback (ডিবাগে সুবিধা হবে)।
    """
    # --- Same filters as leave_summary ---
    q        = (request.GET.get('q') or '').strip()
    status   = (request.GET.get('status') or '').strip()
    dept_id  = (request.GET.get('department') or '').strip()
    d_from   = parse_date(request.GET.get('date_from') or '')
    d_to     = parse_date(request.GET.get('date_to') or '')

    qs = (LeaveRequest.objects
          .select_related('employee', 'employee__department')
          .only('employee__id', 'employee__name', 'employee__department__name',
                'start_date', 'end_date', 'status', 'leave_type', 'remarks')
          .order_by('-start_date', '-id'))

    if q:
        qs = qs.filter(Q(employee__name__icontains=q) | Q(remarks__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if dept_id:
        qs = qs.filter(employee__department_id=dept_id)
    if d_from:
        qs = qs.filter(end_date__gte=d_from)
    if d_to:
        qs = qs.filter(start_date__lte=d_to)

    # --- Same aggregation ---
    bucket = {}
    rows = qs.values('employee_id', 'employee__name', 'start_date', 'end_date', 'leave_type')

    for r in rows:
        emp_id = r['employee_id']
        if emp_id not in bucket:
            bucket[emp_id] = {
                'employee': r['employee__name'],
                'total_days': 0,
                'requests': 0,
                'type_days': defaultdict(int),
            }
        days = _clip_days(r['start_date'], r['end_date'], d_from, d_to)
        if days <= 0:
            continue
        bucket[emp_id]['total_days'] += days
        bucket[emp_id]['requests'] += 1
        if r['leave_type']:
            bucket[emp_id]['type_days'][r['leave_type']] += days

    summary_data = []
    for it in bucket.values():
        summary_data.append({
            'employee': it['employee'],
            'total_days': it['total_days'],
            'requests': it['requests'],
            'type_breakdown': ', '.join(f"{k}:{v}" for k, v in sorted(it['type_days'].items())),
        })
    summary_data.sort(key=lambda x: (x['employee'] or '').lower())

    context = {
        'summary': summary_data,             # ⬅️ PDF টেমপ্লেটে 'summary' ব্যবহার করুন
        'generated_on': date.today(),
        'q': q, 'status': status, 'department': dept_id,
        'date_from': d_from, 'date_to': d_to,
    }

    html_string = render_to_string('leave_summary_pdf.html', context)

    # WeasyPrint না থাকলে HTML fallback
    if HTML is None:
        return HttpResponse(html_string)

    pdf = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = 'inline; filename="leave_summary.pdf"'
    return resp



@login_required
def leave_summary_pdf(request):
    # --- Same filters as leave_summary ---
    q        = (request.GET.get('q') or '').strip()
    status   = (request.GET.get('status') or '').strip()
    dept_id  = (request.GET.get('department') or '').strip()
    d_from   = parse_date(request.GET.get('date_from') or '')
    d_to     = parse_date(request.GET.get('date_to') or '')

    qs = (LeaveRequest.objects
          .select_related('employee', 'employee__department')
          .only('employee__id', 'employee__name', 'employee__department__name',
                'start_date', 'end_date', 'status', 'leave_type', 'remarks')
          .order_by('-start_date', '-id'))

    if q:
        qs = qs.filter(Q(employee__name__icontains=q) | Q(remarks__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if dept_id:
        qs = qs.filter(employee__department_id=dept_id)
    if d_from:
        qs = qs.filter(end_date__gte=d_from)
    if d_to:
        qs = qs.filter(start_date__lte=d_to)

    # --- Aggregate days by employee ---
    bucket = {}
    rows = qs.values('employee_id', 'employee__name', 'start_date', 'end_date', 'leave_type')

    for r in rows:
        emp_id = r['employee_id']
        if emp_id not in bucket:
            bucket[emp_id] = {
                'employee': r['employee__name'],
                'total_days': 0,
                'requests': 0,
                'type_days': defaultdict(int),
            }
        days = _clip_days(r['start_date'], r['end_date'], d_from, d_to)
        if days <= 0:
            continue
        bucket[emp_id]['total_days'] += days
        bucket[emp_id]['requests'] += 1
        if r['leave_type']:
            bucket[emp_id]['type_days'][r['leave_type']] += days

    summary_data = []
    for it in bucket.values():
        summary_data.append({
            'employee': it['employee'],
            'total_days': it['total_days'],
            'requests': it['requests'],
            'type_breakdown': ', '.join(f"{k}:{v}" for k, v in sorted(it['type_days'].items())),
        })
    summary_data.sort(key=lambda x: (x['employee'] or '').lower())

    context = {
        'summary': summary_data,
        'generated_on': date.today(),
        'q': q, 'status': status, 'department': dept_id,
        'date_from': d_from, 'date_to': d_to,
    }

    html_string = render_to_string('leave_summary_pdf.html', context)

    if HTML is None:
        return HttpResponse(html_string)

    pdf = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = 'inline; filename="leave_summary.pdf"'
    return resp


@login_required
def leave_create(request):
    form = LeaveRequestForm(request.POST or None, user=request.user)  # user পাস করছি form এ
    if form.is_valid():
        leave = form.save(commit=False)
        leave.approved_by = request.user
        leave.save()
        return redirect('attendance_app:leave_list')
    return render(request, 'leave_form.html', {'form': form, 'title': 'Create Leave'})


@login_required
def leave_update(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    form = LeaveRequestForm(request.POST or None, instance=leave, user=request.user)
    if form.is_valid():
        form.save()
        return redirect('attendance_app:leave_list')
    return render(request, 'leave_form.html', {'form': form, 'title': 'Update Leave'})


@login_required
def leave_delete(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    leave.delete()
    return redirect('attendance_app:leave_list')


# ----------------Holi day ---------------


@login_required
def holiday_list(request):
    user_company = getattr(request.user.profile, 'company', None)
    if user_company:
        holidays = Holiday.objects.filter(company=user_company)
    else:
        holidays = Holiday.objects.none()  # যদি company না থাকে, কিছু দেখাবেন না
    return render(request, 'holiday_list.html', {'holidays': holidays})

@login_required
def holiday_create(request):
    form = HolidayForm(request.POST or None)
    if form.is_valid():
        holiday = form.save(commit=False)
        user_company = getattr(request.user.profile, 'company', None)
        if user_company:
            holiday.company = user_company
            holiday.save()
            return redirect('attendance_app:holiday_list')
        else:
            messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
            return redirect('attendance_app:holiday_list')
    return render(request, 'holiday_form.html', {'form': form, 'title': 'Add Holiday'})


@login_required
def holiday_edit(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')

    holiday = get_object_or_404(Holiday, pk=pk, company=user_company)
    form = HolidayForm(request.POST or None, instance=holiday)
    if form.is_valid():
        form.save()
        return redirect('attendance_app:holiday_list')

    return render(request, 'holiday_form.html', {'form': form, 'title': 'Edit Holiday'})

@login_required
def holiday_delete(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')

    holiday = get_object_or_404(Holiday, pk=pk, company=user_company)
    if request.method == 'POST':
        holiday.delete()
        return redirect('attendance_app:holiday_list')

    return render(request, 'holiday_confirm_delete.html', {'holiday': holiday})



def custom_404_view(request,exception):
    return render(request, '404.html', status=504)