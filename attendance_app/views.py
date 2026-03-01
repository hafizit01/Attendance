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

    # 3) আজকের অ্যাটেন্ডেন্স (Optimized Query)
    today_att_qs = (Attendance.objects
                    .filter(employee_id__in=emp_ids, timestamp__date=today)
                    .only('employee_id', 'timestamp', 'status')
                    .order_by('employee_id', 'timestamp')) # Time অনুযায়ী sort করা জরুরি

    # Group rows by employee_id in Python
    rows_by_emp = defaultdict(list)
    for att in today_att_qs:
        rows_by_emp[att.employee_id].append(att)

    employee_by_id = {e.id: e for e in employees}

    # Per-employee compute
    employee_data = []
    regular_work_time = timedelta(hours=9) # স্ট্যান্ডার্ড ৯ ঘণ্টা ধরা হলো (চাইলে ১০ করতে পারেন)

    # আমরা সব এমপ্লয়ি লুপ করব, যাতে যারা Absent তাদেরও লিস্টে পাওয়া যায়
    for emp_id in emp_ids:
        emp = employee_by_id.get(emp_id)
        if not emp:
            continue

        recs = rows_by_emp.get(emp_id, [])
        
        # ডিফল্ট ভ্যালু
        first_in = None
        last_out = None
        total_work_time = timedelta()
        late_time = timedelta()
        over_time = timedelta()
        less_time = timedelta()
        status_display = "Absent"

        # --- ✅ Logic: First In - Last Out ---
        if recs:
            # সব টাইমস্ট্যাম্প বের করি
            timestamps = [r.timestamp for r in recs]
            
            # দিনের প্রথম পাঞ্চ = In Time
            first_in = min(timestamps)
            
            # যদি একের বেশি পাঞ্চ থাকে, তবেই Out Time আছে
            if len(timestamps) > 1:
                last_out = max(timestamps)
                
                # Timezone awareness check
                if is_naive(first_in): first_in = make_aware(first_in)
                if is_naive(last_out): last_out = make_aware(last_out)
                
                total_work_time = last_out - first_in
                status_display = "Present"
            else:
                # শুধু একবার পাঞ্চ করেছে (হয়তো মাত্র এসেছে, বা আউট দিতে ভুলে গেছে)
                if is_naive(first_in): first_in = make_aware(first_in)
                status_display = "Present (Active)"

        # --- Late Calculation ---
        if first_in:
            # ডিপার্টমেন্টের টাইম বা ডিফল্ট ১০:৩০
            dept_in_time = getattr(getattr(emp, 'department', None), 'in_time', time(10, 30))
            
            # Compare logic
            expected_start = first_in.replace(hour=dept_in_time.hour, minute=dept_in_time.minute, second=0, microsecond=0)
            
            if first_in > expected_start:
                late_time = first_in - expected_start

        # --- Overtime / Less time ---
        # ডিউটি আওয়ার্স পূর্ণ হয়েছে কিনা
        if total_work_time > regular_work_time:
            over_time = total_work_time - regular_work_time
        elif total_work_time > timedelta(0): 
            # কাজ করেছে কিন্তু সময়ের কম
            less_time = regular_work_time - total_work_time

        # লিস্টে ডাটা যোগ করা
        employee_data.append({
            'employee': emp,
            'in_time': localtime(first_in).time() if first_in else None,
            'out_time': localtime(last_out).time() if last_out else None,
            'total_work_time': total_work_time,
            'late_time': late_time,
            'over_time': over_time,
            'less_time': less_time,
            'status': status_display  # টেমপ্লেটে দেখানোর জন্য
        })

    # 4) ৩০ দিনের ট্রেন্ড (one aggregate query)
    start_date = today - timedelta(days=29)

    # ---- (Q4) Trend Calculation
    trend_agg = (
        Attendance.objects
        .filter(employee_id__in=emp_ids, timestamp__date__gte=start_date, timestamp__date__lte=today)
        .annotate(day=TruncDate('timestamp'))
        .values('day')
        .annotate(
            # Distinct employee count for Present
            present=Count('employee', distinct=True),
            # Simple logic for late in trend (In record after 10:30)
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
        a = total_employees - p # Total - Present = Absent
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
        # Summary counts based on calculated data
        'present': sum(1 for e in employee_data if e['in_time']),
        'absent': sum(1 for e in employee_data if not e['in_time']),
        'late': sum(1 for e in employee_data if e['late_time'].total_seconds() > 0),
        'attendance_trend': attendance_trend,  # optional for debugging
        # safe JSON string for template JS
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

# -------------------------------------------------------------------------
# ✅ ১. Optimized Attendance Table Helper
# -------------------------------------------------------------------------
def generate_attendance_table(employee_qs, start_date_str, end_date_str):
    """
    একবারে সব ডাটা ফেচ করে দ্রুত অ্যাটেন্ডেন্স টেবিল তৈরি করে।
    """
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    
    # একবারে সব ডাটা লোড করা (Optimization)
    att_records = Attendance.objects.filter(
        employee__in=employee_qs,
        timestamp__date__range=(start_date, end_date)
    ).values('employee_id', 'timestamp', 'status')

    # হলিডে লোড (Assuming same company for all in QS)
    first_emp = employee_qs.first()
    company = first_emp.company if first_emp else None
    holidays = Holiday.objects.filter(
        company=company, start_date__lte=end_date, end_date__gte=start_date
    ).values('start_date', 'end_date')

    # ম্যাপ তৈরি করা (O(1) Lookup এর জন্য)
    att_map = defaultdict(list)
    for r in att_records:
        att_map[(r['employee_id'], r['timestamp'].date())].append(r['timestamp'])

    holiday_set = set()
    for h in holidays:
        curr = max(h['start_date'], start_date)
        while curr <= min(h['end_date'], end_date):
            holiday_set.add(curr)
            curr += timedelta(days=1)

    days_list = []
    curr_d = start_date
    while curr_d <= end_date:
        weekday = curr_d.strftime('%A')
        for emp in employee_qs:
            punches = att_map.get((emp.id, curr_d), [])
            is_off = (weekday == emp.department.weekly_off_day) if emp.department else False
            
            in_t = min(punches) if punches else None
            out_t = max(punches) if len(punches) > 1 else None
            
            # Status Priority Logic
            if punches:
                status = "Present"
            elif curr_d in holiday_set:
                status = "Holiday"
            elif is_off:
                status = "Weekly Off"
            else:
                status = "Absent"

            days_list.append({
                'employee': emp,
                'date': curr_d,
                'in_time': in_t,
                'out_time': out_t,
                'status': status
            })
        curr_d += timedelta(days=1)
    return days_list


def get_monthly_report_context(request):
    """
    একটি কমন ফাংশন যা HTML এবং PDF উভয় ভিউর জন্য ডাটা ক্যালকুলেট করবে।
    """
    user = request.user
    user_company = getattr(getattr(user, "profile", None), "company", None)
    
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if not start_date_str or not end_date_str:
        today = timezone.localdate()
        start_date = today - timedelta(days=30)
        end_date = today
    else:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

    selected_dept = request.GET.get('department')
    selected_emp = request.GET.get('employee')

    employees = Employee.objects.filter(company=user_company).select_related('department')
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)
    if selected_emp:
        employees = employees.filter(id=selected_emp)

    holidays = Holiday.objects.filter(company=user_company, start_date__lte=end_date, end_date__gte=start_date)
    holiday_dates = {d for h in holidays for d in [h.start_date + timedelta(days=i) for i in range((min(h.end_date, end_date) - max(h.start_date, start_date)).days + 1)]}

    report_data = []
    REGULAR_WORK_TIME = timedelta(hours=10)

    for emp in employees:
        dept = emp.department
        expected_in = dept.in_time if dept else time(10, 30)
        off_day = dept.weekly_off_day if dept else 'Friday'

        atts = Attendance.objects.filter(employee=emp, timestamp__date__range=(start_date, end_date)).order_by('timestamp')
        att_dict = defaultdict(list)
        for a in atts: att_dict[a.timestamp.date()].append(a.timestamp)

        leaves = LeaveRequest.objects.filter(employee=emp, status='Approved', start_date__lte=end_date, end_date__gte=start_date)
        leave_dates = {d for lv in leaves for d in [lv.start_date + timedelta(days=i) for i in range((min(lv.end_date, end_date) - max(lv.start_date, start_date)).days + 1)]}

        present_days, absent_days, leave_count, holiday_count, off_count = 0, 0, 0, 0, 0
        total_work, total_late, total_over = timedelta(), timedelta(), timedelta()
        
        # 🟢 Expected time ঠিক রাখার জন্য ক্যালেন্ডার ছুটির ভেরিয়েবল
        calendar_holiday_count, calendar_off_count = 0, 0

        days_range = (end_date - start_date).days + 1
        for i in range(days_range):
            curr = start_date + timedelta(days=i)
            wd = curr.strftime('%A')
            punches = att_dict.get(curr, [])

            is_holiday = curr in holiday_dates
            is_off_day = (wd == off_day)

            # 🟢 শুধু Expected Time ক্যালকুলেশনের জন্য
            if is_holiday: calendar_holiday_count += 1
            elif is_off_day: calendar_off_count += 1

            # 🟢 আপনার অরিজিনাল Priority Logic (কোনো চেঞ্জ নেই)
            if punches:
                present_days += 1
                t_in = min(punches)
                t_out = max(punches) if len(punches) > 1 else None
                
                exp_dt = make_aware(datetime.combine(curr, expected_in)) if is_naive(datetime.combine(curr, expected_in)) else datetime.combine(curr, expected_in)
                t_in_aware = make_aware(t_in) if is_naive(t_in) else t_in
                
                if t_in_aware > exp_dt: total_late += (t_in_aware - exp_dt)
                
                if t_out:
                    t_out_aware = make_aware(t_out) if is_naive(t_out) else t_out
                    actual_start = max(t_in_aware, exp_dt)
                    if t_out_aware > actual_start:
                        dur = t_out_aware - actual_start
                        total_work += dur
                        
                        # শুধু ওভারটাইম আপডেট
                        if is_holiday or is_off_day: total_over += dur
                        elif dur > REGULAR_WORK_TIME: total_over += (dur - REGULAR_WORK_TIME)
                        
            elif curr in leave_dates:
                leave_count += 1
                total_work += REGULAR_WORK_TIME
            elif is_holiday: 
                holiday_count += 1
            elif is_off_day: 
                off_count += 1
            else: 
                absent_days += 1

        # 🟢 Expected hours ক্যালেন্ডারের ছুটি দিয়ে হিসাব হবে, ফলে ওভারটাইম ব্যালেন্স মিলবে
        expected_hours = (days_range - calendar_off_count - calendar_holiday_count) * REGULAR_WORK_TIME
        
        def fmt(td):
            s = int(td.total_seconds()); return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

        report_data.append({
            'employee': emp, 'present_days': present_days, 'absent_days': absent_days,
            'weekly_off_days': off_count, 'leave_days': leave_count, 'holiday_days': holiday_count,
            'total_work_hours': fmt(total_work), 'late_time': fmt(total_late),
            'over_time': fmt(total_over), 'less_time': fmt(max(expected_hours - total_work, timedelta())),
            'expected_work_time_excl_off': expected_hours, 'work_time_difference': expected_hours - total_work,
            'start_date': start_date, 'end_date': end_date
        })

    return {
        'report_data': report_data,
        'start_date': start_date, 'end_date': end_date,
        'selected_department': selected_dept, 'selected_employee': selected_emp,
        'departments': Department.objects.filter(company=user_company),
        'employees': Employee.objects.filter(company=user_company),
        'company_name': user_company.name if user_company else "Company"
    }


@login_required
def monthly_work_time_report(request):
    try:
        context = get_monthly_report_context(request)
        return render(request, 'monthly_report.html', context)
    except Exception as e:
        return HttpResponseForbidden(f"Error: {str(e)}")

@login_required
def monthly_work_time_pdf(request):
    try:
        # হেল্পার থেকে ডাটা নিয়ে আসা
        context = get_monthly_report_context(request)
        context['logo_url'] = request.build_absolute_uri('/static/images/logo.png')

        # PDF রেন্ডার করা
        html_string = render_to_string('monthly_work_time_report_pdf.html', context)
        
        if HTML is None:
            return HttpResponse("WeasyPrint is not installed. Cannot generate PDF.", status=500)
            
        pdf = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Monthly_Report_{context["start_date"]}.pdf"'
        return response
    except Exception as e:
        return HttpResponseBadRequest(f"PDF Generation Error: {str(e)}")



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

def generate_attendance_table(employee_qs, start_date_str, end_date_str):
    """
    একবারে সব ডাটা লোড করে ফাস্ট অ্যাটেন্ডেন্স টেবিল জেনারেট করে।
    Priority: Present > Holiday > Leave > Weekly Off > Absent
    """
    # Date parsing (String to Date object)
    if isinstance(start_date_str, str):
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    else:
        start_date = start_date_str

    if isinstance(end_date_str, str):
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    else:
        end_date = end_date_str

    # 1. Bulk Fetch Data
    # ------------------
    all_attendance = Attendance.objects.filter(
        employee__in=employee_qs,
        timestamp__date__range=(start_date, end_date)
    ).values('id', 'employee_id', 'timestamp')

    all_leaves = LeaveRequest.objects.filter(
        employee__in=employee_qs,
        status='Approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    ).values('employee_id', 'start_date', 'end_date')

    # Holiday Fetch (Company specific)
    first_emp = employee_qs.first()
    company = first_emp.company if first_emp else None
    all_holidays = Holiday.objects.filter(
        company=company,
        start_date__lte=end_date,
        end_date__gte=start_date
    ).values('start_date', 'end_date')

    # 2. Data Mapping
    # ---------------
    att_map = defaultdict(list)
    for att in all_attendance:
        d = att['timestamp'].date()
        att_map[(att['employee_id'], d)].append({
            'timestamp': att['timestamp'],
            'id': att['id']
        })

    leave_map = set()
    for lv in all_leaves:
        curr = max(lv['start_date'], start_date)
        lim = min(lv['end_date'], end_date)
        while curr <= lim:
            leave_map.add((lv['employee_id'], curr))
            curr += timedelta(days=1)

    holiday_map = set()
    for h in all_holidays:
        curr = max(h['start_date'], start_date)
        lim = min(h['end_date'], end_date)
        while curr <= lim:
            holiday_map.add(curr)
            curr += timedelta(days=1)

    # 3. Generate List
    # ----------------
    summary = []
    current_date = start_date
    
    while current_date <= end_date:
        weekday = current_date.strftime('%A')
        
        for emp in employee_qs:
            is_weekly_off = (weekday == emp.department.weekly_off_day) if emp.department else False
            punches = att_map.get((emp.id, current_date), [])
            
            # Logic: First In - Last Out
            timestamps = [p['timestamp'] for p in punches]
            in_time = min(timestamps) if timestamps else None
            out_time = max(timestamps) if len(timestamps) > 1 else None
            attendance_id = punches[0]['id'] if punches else None

            # Status Priority
            if timestamps:
                status = "Present"
            elif current_date in holiday_map:
                status = "Holiday"
            elif (emp.id, current_date) in leave_map:
                status = "Leave"
            elif is_weekly_off:
                status = "Weekly Off"
            else:
                status = "Absent"

            summary.append({
                'employee': emp,
                'date': current_date,
                'in_time': in_time,
                'out_time': out_time,
                'status': status,
                'attendance_id': attendance_id,
                'editable': bool(attendance_id)
            })
            
        current_date += timedelta(days=1)

    return summary

@login_required
def attendance_list(request):
    user_company = getattr(request.user.profile, 'company', None)
    
    # 1. Company Check
    if not user_company:
        messages.error(request, "Company not found.")
        employees = Employee.objects.none()
    else:
        employees = Employee.objects.filter(company=user_company).select_related('department')

    # 2. Filter Inputs
    start_date_str = request.GET.get('start_date') or date.today().strftime('%Y-%m-%d')
    end_date_str = request.GET.get('end_date') or date.today().strftime('%Y-%m-%d')
    
    emp_id = request.GET.get('employee')
    dept_id = request.GET.get('department')

    # 3. Apply Filters
    if emp_id:
        employees = employees.filter(id=emp_id)
    if dept_id:
        employees = employees.filter(department_id=dept_id)

    # 4. Generate Data (Using Optimized Helper)
    attendance_summary = generate_attendance_table(employees, start_date_str, end_date_str)

    # 5. Context Data
    departments = Department.objects.filter(company=user_company) if user_company else []

    return render(request, 'attendance_list.html', {
        'attendance_summary': attendance_summary,
        'employees': employees, # For filter persistence if needed (optional)
        'all_employees': Employee.objects.filter(company=user_company), # For dropdown
        'departments': departments,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'selected_employee': int(emp_id) if emp_id else None,
        'selected_department': int(dept_id) if dept_id else None,
    })


# -------------------------------------------------------------------------
# ✅ Optimized Attendance List PDF (Consistent with Report View)
# -------------------------------------------------------------------------
@login_required
def attendance_list_pdf(request):
    """
    Attendance -> PDF (inline). 
    Uses the optimized `generate_attendance_table` helper for consistency.
    """
    
    # 1. Company Scope Check
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        return HttpResponseForbidden("Company not set.")

    # 2. Get Filters
    start_date = request.GET.get('start_date') or date.today().strftime('%Y-%m-%d')
    end_date = request.GET.get('end_date') or date.today().strftime('%Y-%m-%d')
    
    employee_id = request.GET.get('employee')
    department_id = request.GET.get('department')

    # 3. Filter Employees
    employees = Employee.objects.filter(company=user_company).select_related('department')
    if employee_id:
        employees = employees.filter(id=employee_id)
    if department_id:
        employees = employees.filter(department__id=department_id)

    # 4. Generate Data using the SAME helper as HTML view
    # (This ensures "First In - Last Out" logic is applied uniformly)
    attendance_summary = generate_attendance_table(employees, start_date, end_date)

    # 5. Format Data for PDF Template
    rows = []
    total_seconds = 0

    for r in attendance_summary:
        emp = r['employee']
        in_time = r['in_time']
        out_time = r['out_time']
        
        # Formatting Times
        in_str = localtime(in_time).strftime('%H:%M') if in_time else "-"
        out_str = localtime(out_time).strftime('%H:%M') if out_time else "-"
        
        # Calculate Duration
        duration_str = "-"
        if in_time and out_time:
            # Naive to Aware check handled in helper, but double check for subtraction
            if is_naive(in_time): in_time = make_aware(in_time)
            if is_naive(out_time): out_time = make_aware(out_time)
            
            diff = (out_time - in_time).total_seconds()
            if diff > 0:
                total_seconds += diff
                h, rem = divmod(diff, 3600)
                m, _ = divmod(rem, 60)
                duration_str = f"{int(h):02}:{int(m):02}"

        rows.append({
            'employee_name': emp.name,
            'department_name': emp.department.name if emp.department else "-",
            'date': r['date'],
            'check_in': in_str,
            'check_out': out_str,
            'worked_hours': duration_str,
            'status': r['status']
        })

    # Total Worked Hours Calculation
    th, tr = divmod(total_seconds, 3600)
    tm, _ = divmod(tr, 60)
    total_worked = f"{int(th):02}:{int(tm):02}"

    # 6. Render PDF
    context = {
        'rows': rows,
        'start_date': start_date,
        'end_date': end_date,
        'user': request.user,
        'total_worked': total_worked,
        'company_name': user_company.name,
        'logo_url': request.build_absolute_uri('/static/images/logo.png'),
    }

    html_string = render_to_string('attendance_list_pdf.html', context)

    if HTML is None:
        return HttpResponse("WeasyPrint library not installed.", status=500)

    pdf_io = BytesIO()
    HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf(target=pdf_io)
    pdf_io.seek(0)

    response = FileResponse(pdf_io, content_type='application/pdf', as_attachment=False)
    response['Content-Disposition'] = f'inline; filename="attendance_{start_date}_to_{end_date}.pdf"'
    return response



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

from dateutil import parser  # নিশ্চিত করুন এটি ইমপোর্ট করা আছে
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta, date, time as _time
import calendar
from django.utils.timezone import make_aware, is_naive, localtime

@login_required
def employee_attendance_detail(request, employee_id):
    # ১. ফাস্ট ডাটা ফেচিং (Select Related)
    employee = get_object_or_404(Employee.objects.select_related('department'), id=employee_id)
    user_company = getattr(request.user.profile, 'company', None)

    # ২. ফ্লেক্সিবল ডেট পার্সিং (Date Parsing Fix)
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    today = date.today()

    try:
        if start_date_str and end_date_str:
            # এটি যেকোনো ফরম্যাট (Dec 1, 2025 বা 2025-12-01) হ্যান্ডেল করবে
            start_date = parser.parse(start_date_str).date()
            end_date = parser.parse(end_date_str).date()
        else:
            start_date = today.replace(day=1)
            end_date = today
    except (ValueError, TypeError, parser.ParserError):
        # এরর হলে সেইফ ডিফল্ট
        start_date = today.replace(day=1)
        end_date = today

    # ৩. বেস সামারি তৈরি (লুপের গতি বাড়ানো হয়েছে)
    summary = OrderedDict()
    weekly_off_day = employee.department.weekly_off_day if employee.department else "Friday"
    
    # একবারে সব দিনের স্ট্রাকচার তৈরি
    curr = start_date
    while curr <= end_date:
        summary[curr] = {
            'in_time': None, 
            'out_time': None,
            'status': 'Holiday' if curr.strftime('%A') == weekly_off_day else 'Absent'
        }
        curr += timedelta(days=1)

    # ৪. পাবলিক হলিডে লোড (Bulk Load & Map) [Performance Fix]
    holidays = Holiday.objects.filter(
        company=user_company, 
        start_date__lte=end_date, 
        end_date__gte=start_date
    )
    for h in holidays:
        # রেঞ্জ ক্ল্যাম্পিং (যাতে লুপ কম চলে)
        h_start = max(h.start_date, start_date)
        h_end = min(h.end_date, end_date)
        curr = h_start
        while curr <= h_end:
            if curr in summary:
                summary[curr]['status'] = 'Public Holiday'
            curr += timedelta(days=1)

    # ৫. অ্যাটেন্ডেন্স প্রসেসিং (Fast Mapping)
    attendances = Attendance.objects.filter(
        employee=employee,
        timestamp__date__range=(start_date, end_date)
    ).values('timestamp')  # শুধু timestamp আনলে কোয়েরি ফাস্ট হবে

    daily_punches = defaultdict(list)
    for att in attendances:
        ts = att['timestamp']
        daily_punches[ts.date()].append(ts)

    cutoff_time = _time(14, 0) # দুপুর ২টা

    for d, punches in daily_punches.items():
        if d not in summary: continue
        
        # পাঞ্চ থাকলে স্ট্যাটাস প্রেজেন্ট (ওভাররাইড)
        summary[d]['status'] = 'Present'
        
        # টাইমজোন ফিক্স করে সর্ট করা
        punches = sorted([localtime(p) for p in punches])
        
        # ফাস্ট ফিল্টারিং
        before_14 = [p for p in punches if p.time() < cutoff_time]
        after_14 = [p for p in punches if p.time() >= cutoff_time]

        in_time = None
        out_time = None

        if before_14:
            in_time = before_14[0]
            if after_14:
                out_time = after_14[-1]
            elif len(before_14) > 1:
                out_time = before_14[-1]
        else:
            # সব পাঞ্চ ২টার পরে হলে
            if len(after_14) == 1:
                out_time = after_14[0] # শুধু একটা পাঞ্চ বিকেলে হলে সেটা আউট
            elif len(after_14) > 1:
                in_time = after_14[0]
                out_time = after_14[-1]

        summary[d]['in_time'] = in_time
        summary[d]['out_time'] = out_time

    # ৬. লিভ প্রসেসিং (Punch এর পরে চেক, যাতে প্রেজেন্ট ওভাররাইড না হয়)
    leaves = LeaveRequest.objects.filter(
        employee=employee, 
        status='Approved', 
        start_date__lte=end_date, 
        end_date__gte=start_date
    )
    for lv in leaves:
        l_start = max(lv.start_date, start_date)
        l_end = min(lv.end_date, end_date)
        curr = l_start
        while curr <= l_end:
            # যদি প্রেজেন্ট না থাকে তবেই লিভ বসবে
            if curr in summary and summary[curr]['status'] != 'Present':
                summary[curr]['status'] = 'Leave'
            curr += timedelta(days=1)

    # ৭. ক্যালকুলেশন ও স্ট্যাটস
    total_work_duration = timedelta()
    dept = employee.department
    
    # শিফট ডিউরেশন এবং টাইম
    exp_in = dept.in_time if dept and dept.in_time else _time(10, 30)
    exp_out = dept.out_time if dept and dept.out_time else _time(20, 30)
    
    # শিফট ডিউরেশন ক্যালকুলেশন (একবারই করা ভালো)
    dummy_date = date.min
    shift_dur = datetime.combine(dummy_date, exp_out) - datetime.combine(dummy_date, exp_in)
    if shift_dur.total_seconds() < 0: shift_dur = timedelta(0)

    # কাউন্টার (লুপের ভেতরে না চালিয়ে জেনারেটর এক্সপ্রেশন ব্যবহার - ফাস্ট)
    status_values = [d['status'] for d in summary.values()]
    
    for d, data in summary.items():
        if data['status'] == 'Present' and data['in_time'] and data['out_time']:
            t_in = data['in_time']
            t_out = data['out_time']
            
            # Aware/Naive ফিক্স
            if is_naive(t_in): t_in = make_aware(t_in)
            if is_naive(t_out): t_out = make_aware(t_out)
            
            # লেট এডজাস্টমেন্ট
            exp_in_dt = make_aware(datetime.combine(d, exp_in)) if is_naive(datetime.combine(d, exp_in)) else datetime.combine(d, exp_in)
            
            actual_start = max(t_in, exp_in_dt)
            if t_out > actual_start:
                total_work_duration += (t_out - actual_start)
        
        elif data['status'] == 'Leave':
            total_work_duration += shift_dur

    # ৮. কনটেক্সট রিটার্ন
    context = {
        'employee': employee,
        'attendance_summary': summary.items(),
        'start_date': start_date.strftime("%Y-%m-%d"), # স্ট্রিং হিসেবে পাঠানো হচ্ছে যাতে ফর্মে ভ্যালু থাকে
        'end_date': end_date.strftime("%Y-%m-%d"),
        'total_work_duration': total_work_duration,
        
        'approved_leave_count': status_values.count('Leave'),
        'absent_days': status_values.count('Absent'),
        'public_holiday_count': status_values.count('Public Holiday'),
        'weekly_holiday_count': status_values.count('Holiday'),
        
        'total_leave_requests': leaves.count(),
    }

    return render(request, 'employee_attendance_detail.html', context)



# ----------------------attendance details pdf-------------

from dateutil import parser
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta, date, time as _time
from django.utils.timezone import make_aware, localtime, is_naive
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from weasyprint import HTML

# হেল্পার ফাংশন: টাইমডেল্টা ফরম্যাটিং
def format_timedelta(td):
    if not isinstance(td, timedelta): return "00:00:00"
    total_seconds = int(td.total_seconds())
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    h, r = divmod(total_seconds, 3600)
    m, s = divmod(r, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"

@login_required
def employee_attendance_pdf(request, employee_id):
    # ১. ডাটা ফেচিং
    emp = get_object_or_404(Employee.objects.select_related('department'), id=employee_id)
    user_company = getattr(request.user.profile, 'company', None)

    # ২. ডেট রেঞ্জ (Flexible Parsing Fix)
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    today = date.today()

    try:
        if start_date_str and end_date_str:
            start_date = parser.parse(start_date_str).date()
            end_date = parser.parse(end_date_str).date()
        else:
            start_date = today.replace(day=1)
            end_date = today
    except (ValueError, TypeError, parser.ParserError):
        start_date = today.replace(day=1)
        end_date = today

    # ৩. শিফট টাইম সেটআপ
    dept = emp.department
    exp_in = dept.in_time if dept and dept.in_time else _time(10, 30)
    exp_out = dept.out_time if dept and dept.out_time else _time(20, 30)
    
    # শিফট ডিউরেশন
    dummy = date.min
    shift_dur = datetime.combine(dummy, exp_out) - datetime.combine(dummy, exp_in)
    if shift_dur.total_seconds() < 0: shift_dur = timedelta(0)

    # ৪. ডাটা লোডিং (Bulk)
    # -- Attendance --
    attendance_qs = Attendance.objects.filter(
        employee=emp, timestamp__date__range=(start_date, end_date)
    ).values('timestamp') # Values ব্যবহার করলে ফাস্ট হবে
    
    daily_punches = defaultdict(list)
    for att in attendance_qs:
        daily_punches[att['timestamp'].date()].append(att['timestamp'])

    # -- Holidays --
    holidays = Holiday.objects.filter(company=user_company, start_date__lte=end_date, end_date__gte=start_date)
    holiday_dates = set()
    for h in holidays:
        curr = max(h.start_date, start_date)
        lim = min(h.end_date, end_date)
        while curr <= lim:
            holiday_dates.add(curr)
            curr += timedelta(days=1)

    # -- Leaves --
    leaves = LeaveRequest.objects.filter(employee=emp, status='Approved', start_date__lte=end_date, end_date__gte=start_date)
    leave_dates = set()
    for l in leaves:
        curr = max(l.start_date, start_date)
        lim = min(l.end_date, end_date)
        while curr <= lim:
            leave_dates.add(curr)
            curr += timedelta(days=1)

    # ৫. লুপ এবং ক্যালকুলেশন
    summary_list = []
    total_work = timedelta()
    total_over = timedelta()
    total_less = timedelta()
    
    counts = {'Present': 0, 'Absent': 0, 'Leave': 0, 'Holiday': 0, 'Weekly Off': 0}
    off_day = dept.weekly_off_day if dept else None
    cutoff_time = _time(14, 0)

    curr_date = start_date
    while curr_date <= end_date:
        weekday = curr_date.strftime('%A')
        punches = daily_punches.get(curr_date, [])
        
        status = "Absent"
        in_time_val = None
        out_time_val = None
        daily_w = timedelta()
        daily_o = timedelta()
        daily_l = timedelta()

        # Priority Logic: Present > Leave > Holiday > Off > Absent
        if punches:
            status = "Present"
            counts['Present'] += 1
            
            # Sort & Timezone Localize
            punches = sorted([localtime(p) for p in punches])
            
            # Logic: Before 14:00 is IN, After is OUT
            before_14 = [p for p in punches if p.time() < cutoff_time]
            after_14 = [p for p in punches if p.time() >= cutoff_time]

            if before_14:
                in_time_val = before_14[0]
                out_time_val = after_14[-1] if after_14 else (before_14[-1] if len(before_14) > 1 else None)
            else:
                if len(after_14) > 1:
                    in_time_val = after_14[0]
                    out_time_val = after_14[-1]
                else:
                    out_time_val = after_14[0]

            # Work Calculation
            if in_time_val and out_time_val:
                exp_in_dt = make_aware(datetime.combine(curr_date, exp_in)) if is_naive(datetime.combine(curr_date, exp_in)) else datetime.combine(curr_date, exp_in)
                actual_start = max(in_time_val, exp_in_dt)
                
                if out_time_val > actual_start:
                    daily_w = out_time_val - actual_start
            
            # 🟢 Overtime / Less Logic (UPDATED)
            is_off_day = (off_day and weekday == off_day)
            is_pub_holiday = (curr_date in holiday_dates)

            if is_off_day or is_pub_holiday:
                # ছুটির দিন বা সাপ্তাহিক বন্ধে আসলে পুরো কাজটাই ওভারটাইম
                daily_o = daily_w
                daily_l = timedelta(0) # ছুটির দিনে কোনো Less Time পেনাল্টি নেই
                
                # রিপোর্টে বোঝার সুবিধার জন্য স্ট্যাটাস পরিবর্তন
                if is_off_day: 
                    status = "Present (Weekly Off)"
                else: 
                    status = "Present (Holiday)"
            else:
                # রেগুলার কর্মদিবসের হিসাব
                if daily_w > shift_dur:
                    daily_o = daily_w - shift_dur
                    daily_l = timedelta(0) # সেফটির জন্য যুক্ত করা হলো
                else:
                    daily_o = timedelta(0) # সেফটির জন্য যুক্ত করা হলো
                    daily_l = shift_dur - daily_w

        elif curr_date in leave_dates:
            status = "Leave" # or "On Leave"
            counts['Leave'] += 1
            daily_w = shift_dur # Full credit

        elif curr_date in holiday_dates:
            status = "Public Holiday"
            counts['Holiday'] += 1
        
        elif off_day and weekday == off_day:
            status = "Weekly Off"
            counts['Weekly Off'] += 1
        
        else:
            status = "Absent"
            counts['Absent'] += 1
            daily_l = shift_dur # Full penalty

        # Totals
        total_work += daily_w
        total_over += daily_o
        total_less += daily_l

        summary_list.append({
            'date': curr_date,
            'weekday': weekday,
            'status': status,
            'in_time': in_time_val.strftime('%I:%M %p') if in_time_val else "-",
            'out_time': out_time_val.strftime('%I:%M %p') if out_time_val else "-",
            'work_time': format_timedelta(daily_w),
            'over_time': format_timedelta(daily_o),
            'less_time': format_timedelta(daily_l),
        })
        
        curr_date += timedelta(days=1)

    # ৬. পিডিএফ রেন্ডার
    context = {
        'employee': emp,
        'start_date': start_date,
        'end_date': end_date,
        'summary': summary_list,
        'stats': {
            'present': counts['Present'],
            'absent': counts['Absent'],
            'leave': counts['Leave'],
            'holiday': counts['Holiday'] + counts['Weekly Off'], # Holidays = Public + Weekly
            'total_work': format_timedelta(total_work),
            'total_over': format_timedelta(total_over),
            'total_less': format_timedelta(total_less),
        },
        'company_name': user_company.name if user_company else "Attendance System"
    }

    html_string = render_to_string('details_pdf_template.html', context)
    
    # Base URL for static files (images/css)
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
    
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Attendance_{emp.name}_{start_date}.pdf"'
    return response


# ----------attendance_pdf_report-------------
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.utils.timezone import make_aware, localtime, is_naive
from datetime import datetime, timedelta, date, time as _time
from collections import defaultdict
from dateutil import parser  # pip install python-dateutil
from weasyprint import HTML
from .models import Attendance, Employee, Holiday, LeaveRequest

# হেল্পার: টাইম ফরম্যাটিং
def format_timedelta(td):
    if not isinstance(td, timedelta): return "00:00:00"
    total_seconds = int(td.total_seconds())
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    h, r = divmod(total_seconds, 3600)
    m, s = divmod(r, 60)
    return f"{sign}{h:02d}:{m:02d}:{s:02d}"

@login_required
def attendance_pdf_report(request, employee_id):
    # ১. ফাস্ট ডাটা ফেচিং
    emp = get_object_or_404(Employee.objects.select_related('department'), id=employee_id)
    user_company = getattr(request.user.profile, 'company', None)

    # ২. ফ্লেক্সিবল ডেট পার্সিং (Date Fix)
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    today = date.today()

    try:
        if start_date_str and end_date_str:
            start_date = parser.parse(start_date_str).date()
            end_date = parser.parse(end_date_str).date()
        else:
            start_date = today.replace(day=1)
            end_date = today
    except (ValueError, TypeError, parser.ParserError):
        start_date = today.replace(day=1)
        end_date = today

    # ৩. শিফট ও লজিক সেটআপ
    dept = emp.department
    exp_in = dept.in_time if dept and dept.in_time else _time(10, 30)
    exp_out = dept.out_time if dept and dept.out_time else _time(20, 30)
    
    dummy = date.min
    shift_dur = datetime.combine(dummy, exp_out) - datetime.combine(dummy, exp_in)
    if shift_dur.total_seconds() < 0: shift_dur = timedelta(0)

    # ৪. বাল্ক ডাটা লোডিং (Performance Boost 🚀)
    # -- Attendance --
    attendance_qs = Attendance.objects.filter(
        employee=emp, timestamp__date__range=(start_date, end_date)
    ).values('timestamp') # values() ব্যবহার করলে মেমোরি কম লাগে
    
    daily_punches = defaultdict(list)
    for att in attendance_qs:
        daily_punches[att['timestamp'].date()].append(att['timestamp'])

    # -- Holidays --
    holidays = Holiday.objects.filter(company=user_company, start_date__lte=end_date, end_date__gte=start_date)
    holiday_dates = set()
    for h in holidays:
        curr = max(h.start_date, start_date)
        lim = min(h.end_date, end_date)
        while curr <= lim:
            holiday_dates.add(curr)
            curr += timedelta(days=1)

    # -- Leaves --
    leaves = LeaveRequest.objects.filter(employee=emp, status='Approved', start_date__lte=end_date, end_date__gte=start_date)
    leave_dates = set()
    for l in leaves:
        curr = max(l.start_date, start_date)
        lim = min(l.end_date, end_date)
        while curr <= lim:
            leave_dates.add(curr)
            curr += timedelta(days=1)

    # ৫. মেইন প্রসেসিং লুপ
    summary_list = []
    total_work = timedelta()
    total_over = timedelta()
    total_less = timedelta()
    
    counts = {'Present': 0, 'Absent': 0, 'Leave': 0, 'Holiday': 0}
    off_day = dept.weekly_off_day if dept else None
    cutoff_time = _time(14, 0)

    curr_date = start_date
    while curr_date <= end_date:
        weekday = curr_date.strftime('%A')
        punches = daily_punches.get(curr_date, [])
        
        status = "Absent"
        in_time_val = None
        out_time_val = None
        daily_w = timedelta()
        daily_o = timedelta()
        daily_l = timedelta()

        # Priority: Present > Leave > Holiday > Off > Absent
        if punches:
            status = "Present"
            counts['Present'] += 1
            punches = sorted([localtime(p) for p in punches])
            
            # Logic: Before 14:00 -> IN, After 14:00 -> OUT
            before_14 = [p for p in punches if p.time() < cutoff_time]
            after_14 = [p for p in punches if p.time() >= cutoff_time]

            if before_14:
                in_time_val = before_14[0]
                out_time_val = after_14[-1] if after_14 else (before_14[-1] if len(before_14) > 1 else None)
            else:
                if len(after_14) > 1:
                    in_time_val = after_14[0]
                    out_time_val = after_14[-1]
                else:
                    out_time_val = after_14[0]

            # Work Calculation
            if in_time_val and out_time_val:
                exp_in_dt = make_aware(datetime.combine(curr_date, exp_in)) if is_naive(datetime.combine(curr_date, exp_in)) else datetime.combine(curr_date, exp_in)
                actual_start = max(in_time_val, exp_in_dt)
                
                if out_time_val > actual_start:
                    daily_w = out_time_val - actual_start
            
            # 🟢 Overtime / Less Logic (UPDATED)
            is_off_day = (off_day and weekday == off_day)
            is_pub_holiday = (curr_date in holiday_dates)

            if is_off_day or is_pub_holiday:
                # ছুটির দিন বা সাপ্তাহিক বন্ধে আসলে পুরো কাজটাই ওভারটাইম
                daily_o = daily_w
                daily_l = timedelta(0) # ছুটির দিনে কোনো Less Time পেনাল্টি নেই
                
                # রিপোর্টে বোঝার সুবিধার জন্য স্ট্যাটাস পরিবর্তন
                if is_off_day: 
                    status = "Present (Weekly Off)"
                else: 
                    status = "Present (Holiday)"
            else:
                # রেগুলার কর্মদিবসের হিসাব
                if daily_w > shift_dur:
                    daily_o = daily_w - shift_dur
                    daily_l = timedelta(0) # সেফটির জন্য যুক্ত করা হলো
                else:
                    daily_o = timedelta(0) # সেফটির জন্য যুক্ত করা হলো
                    daily_l = shift_dur - daily_w

        elif curr_date in leave_dates:
            status = "On Leave"
            counts['Leave'] += 1
            daily_w = shift_dur # Full Credit

        elif curr_date in holiday_dates:
            status = "Holiday"
            counts['Holiday'] += 1
        
        elif off_day and weekday == off_day:
            status = "Weekly Off"
            counts['Holiday'] += 1
        
        else:
            status = "Absent"
            counts['Absent'] += 1
            daily_l = shift_dur # Full Penalty

        total_work += daily_w
        total_over += daily_o
        total_less += daily_l

        summary_list.append({
            'date': curr_date,
            'weekday': weekday,
            'status': status,
            'in_time': in_time_val.strftime('%I:%M %p') if in_time_val else "-",
            'out_time': out_time_val.strftime('%I:%M %p') if out_time_val else "-",
            'work_time': format_timedelta(daily_w),
            'over_time': format_timedelta(daily_o),
            'less_time': format_timedelta(daily_l),
        })
        curr_date += timedelta(days=1)

    # ৬. PDF রেন্ডারিং (WeasyPrint)
    context = {
        'employee': emp,
        'start_date': start_date,
        'end_date': end_date,
        'summary': summary_list,
        'stats': {
            'present': counts['Present'],
            'absent': counts['Absent'],
            'leave': counts['Leave'],
            'holiday': counts['Holiday'],
            'total_work': format_timedelta(total_work),
            'total_over': format_timedelta(total_over),
            'total_less': format_timedelta(total_less),
        },
        'company_name': user_company.name if user_company else "Attendance System",
        # লোগোর জন্য ফুল পাথ জরুরি
        'logo_url': request.build_absolute_uri('/static/images/logo.png'), 
    }

    # Template Reuse: আগের টেমপ্লেটটিই ব্যবহার করছি
    html_string = render_to_string('details_pdf_template.html', context)
    
    # PDF তৈরি
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf()
    
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Attendance_{emp.name}_{start_date}.pdf"'
    return response



# --------------Leave Request--------

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.db.models import Q
from django.template.loader import render_to_string
from django.contrib import messages
from collections import defaultdict
from datetime import datetime, date
from dateutil import parser  # pip install python-dateutil

# আপনার অ্যাপের মডেল ইম্পোর্ট
from .models import LeaveRequest, Department, Employee
from .forms import LeaveRequestForm

# WeasyPrint সেটআপ
try:
    from weasyprint import HTML
except ImportError:
    HTML = None

# ---------------------------------------------------------
# 1. Helper Functions (Business Logic)
# ---------------------------------------------------------

def _clip_days(start, end, d_from, d_to):
    """
    Overlap window ধরে inclusive দিন গণনা।
    """
    if d_from:
        start = max(start, d_from)
    if d_to:
        end = min(end, d_to)
    
    if end < start:
        return 0
    return (end - start).days + 1

def get_leave_summary_data(user, get_params):
    """
    HTML এবং PDF উভয়ের জন্য কমন ডাটা প্রসেসিং ফাংশন।
    রিটার্ন করে: context dictionary
    """
    user_company = getattr(user.profile, 'company', None)
    if not user_company:
        return {'error': 'User has no company assigned.'}

    # Filters
    q = (get_params.get('q') or '').strip()
    status = (get_params.get('status') or '').strip()
    dept_id = (get_params.get('department') or '').strip()
    
    # Date Parsing (Robust)
    d_from_str = get_params.get('date_from')
    d_to_str = get_params.get('date_to')
    d_from, d_to = None, None

    try:
        if d_from_str: d_from = parser.parse(d_from_str).date()
        if d_to_str: d_to = parser.parse(d_to_str).date()
    except (ValueError, TypeError):
        pass

    # Base Queryset (Company Scoped)
    qs = LeaveRequest.objects.filter(company=user_company).select_related(
        'employee', 'employee__department'
    ).only(
        'employee__id', 'employee__name', 'employee__department__name',
        'start_date', 'end_date', 'status', 'leave_type', 'reason' # 🟢 remarks পরিবর্তন করে reason করা হয়েছে
    ).order_by('-start_date', '-id')

    # Apply Filters
    if q:
        # 🟢 remarks__icontains পরিবর্তন করে reason__icontains করা হয়েছে
        qs = qs.filter(Q(employee__name__icontains=q) | Q(reason__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if dept_id:
        qs = qs.filter(employee__department_id=dept_id)
    
    # Overlap Filter (Database level optimize)
    if d_from:
        qs = qs.filter(end_date__gte=d_from)
    if d_to:
        qs = qs.filter(start_date__lte=d_to)

    # Aggregation Logic
    bucket = {}
    # .values() for performance
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
        
        l_type = r['leave_type'] or 'Unspecified'
        bucket[emp_id]['type_days'][l_type] += days

    # List Formatting
    summary = []
    for it in bucket.values():
        summary.append({
            'employee': it['employee'],
            'total_days': it['total_days'],
            'requests': it['requests'],
            'type_breakdown': ', '.join(f"{k}: {v}" for k, v in sorted(it['type_days'].items())),
        })
    
    summary.sort(key=lambda x: (x['employee'] or '').lower())

    return {
        'summary': summary,
        'q': q, 'status': status, 'department': dept_id,
        'date_from': d_from, 'date_to': d_to,
        'departments': Department.objects.filter(company=user_company).only('id', 'name').order_by('name'),
        'generated_on': date.today(),
        'company_name': user_company.name
    }

# ---------------------------------------------------------
# 2. Views
# ---------------------------------------------------------

@login_required
def leave_list(request):
    user_company = getattr(request.user.profile, 'company', None)
    # Company filter added
    leaves = LeaveRequest.objects.filter(company=user_company).select_related('employee')

    query = request.GET.get('q', '')
    if query:
        leaves = leaves.filter(employee__name__icontains=query)

    context = {
        'leaves': leaves,
        'query': query
    }

    if request.headers.get('HX-Request') == 'true':
        return render(request, 'partials/leave_table.html', context)

    return render(request, 'leave_list.html', context)


@login_required
def leave_summary(request):
    """ UI View: Uses helper function """
    context = get_leave_summary_data(request.user, request.GET)
    if 'error' in context:
        return HttpResponseForbidden(context['error'])
        
    return render(request, 'leave_summary.html', context)


@login_required
def leave_summary_pdf(request):
    """ PDF View: Uses same helper function """
    context = get_leave_summary_data(request.user, request.GET)
    
    if 'error' in context:
        return HttpResponseForbidden(context['error'])

    # Add absolute URI for images in PDF
    context['logo_url'] = request.build_absolute_uri('/static/images/logo.png')

    html_string = render_to_string('leave_summary_pdf.html', context)

    if HTML is None:
        return HttpResponse("WeasyPrint not installed. Showing HTML version.<br>" + html_string)

    pdf = HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()
    
    filename = f"Leave_Summary_{date.today()}.pdf"
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
def leave_create(request):
    user_company = getattr(request.user.profile, 'company', None)
    
    # Form এ user/company পাস করা হচ্ছে (যদি ফর্ম কাস্টমাইজড থাকে)
    form = LeaveRequestForm(request.POST or None, user=request.user) 
    
    if form.is_valid():
        leave = form.save(commit=False)
        leave.approved_by = request.user
        leave.company = user_company  # ✅ Ensure Company is set
        leave.save()
        messages.success(request, "Leave request created successfully.")
        return redirect('attendance_app:leave_list')
        
    return render(request, 'leave_form.html', {'form': form, 'title': 'Create Leave'})


@login_required
def leave_update(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    # ✅ Security: Only own company's data
    leave = get_object_or_404(LeaveRequest, pk=pk, company=user_company)
    
    form = LeaveRequestForm(request.POST or None, instance=leave, user=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, "Leave updated successfully.")
        return redirect('attendance_app:leave_list')
        
    return render(request, 'leave_form.html', {'form': form, 'title': 'Update Leave'})


@login_required
def leave_delete(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    # ✅ Security: Only own company's data
    leave = get_object_or_404(LeaveRequest, pk=pk, company=user_company)
    
    if request.method == 'POST':
        leave.delete()
        messages.warning(request, "Leave request deleted.")
        
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

from django.core.mail import send_mail, BadHeaderError
from django.conf import settings
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.urls import reverse
import random, string

@csrf_exempt
def support_page(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        email = request.POST.get('email')
        subject = request.POST.get('subject')
        message = request.POST.get('message')
        priority = request.POST.get('priority')

        ticket_id = 'TKT-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))

        body = f"""
📩 নতুন সাপোর্ট টিকিট পাওয়া গেছে!

Ticket ID: {ticket_id}
Name: {name}
Email: {email}
Priority: {priority}

Message:
{message}
        """

        try:
            send_mail(
                subject=f"[{ticket_id}] {subject}",
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=['info@easycodingbd.com'],
                fail_silently=False,
            )

            # যদি AJAX হলে JSON রিটার্ন করবো
            is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
            if is_ajax:
                return JsonResponse({
                    'status': 'success',
                    'ticket_id': ticket_id,
                    'message': '✅ আপনার টিকিট সফলভাবে সাবমিট হয়েছে।'
                })

            # নরমাল POST হলে redirect করে success পেজে পাঠাও
            return redirect(f"{reverse('attendance_app:support_success')}?ticket_id={ticket_id}")

        except BadHeaderError:
            return HttpResponseBadRequest("Invalid header found.")
        except Exception as e:
            # production-এ logger.exception(e) ব্যবহার করো
            is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': f'ইমেইল পাঠাতে ব্যর্থ: {str(e)}'})
            return render(request, 'support.html', {'error': 'ইমেইল পাঠাতে ব্যর্থ হয়েছে। অনুগ্রহ করে পরে চেষ্টা করুন।'})

    # GET হলে ফর্ম দেখাবে
    return render(request, 'support.html')


def support_success(request):
    ticket_id = request.GET.get('ticket_id', '')
    message = "আপনার টিকিট সফলভাবে সাবমিট হয়েছে। আমাদের টিম দ্রুত যোগাযোগ করবে।"
    return render(request, 'support_success.html', {'ticket_id': ticket_id, 'message': message})


def custom_404_view(request,exception):
    return render(request, '404.html', status=504)