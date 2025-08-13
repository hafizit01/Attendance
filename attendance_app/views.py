from datetime import datetime, timedelta, time as dtime,time
from collections import defaultdict,OrderedDict
from django.contrib.auth.decorators import login_required
from attendance_app.utils.zk_import import import_attendance
from weasyprint import HTML
from .forms import *
from django.shortcuts import render, redirect,get_object_or_404
from django.http import HttpResponse
from django.contrib import messages
from django.utils.timezone import localtime, localdate
from django.db.models import Q, Min, Max
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from django.utils.timezone import make_aware, is_naive
from .models import *
from django.template.loader import render_to_string
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from django.db.models import Q
import calendar
from reportlab.lib.pagesizes import letter

from django.utils.timezone import now
from django.shortcuts import render
from django.utils.timezone import now, localdate, localtime
from datetime import timedelta
from django.contrib.auth.decorators import login_required

from .models import Employee, Department, Attendance

from subscription_app.models import UserSubscription

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.timezone import now, localtime, localdate
from datetime import timedelta
from .models import Employee, Attendance, Department

@login_required
def dashboard(request):
    user = request.user

    # ----------------------------
    # সাবস্ক্রিপশন চেক
    # ----------------------------
    subscription = UserSubscription.objects.filter(
        user=user,
        active=True,
        end_date__gt=now()
    ).first()

    if not subscription:
        last_sub = UserSubscription.objects.filter(user=user).order_by('-end_date').first()
        return render(request, 'subscription_app/expired.html', {
            'last_end_date': last_sub.end_date if last_sub else None
        })

    # ----------------------------
    # কোম্পানি ভিত্তিক ডিপার্টমেন্ট ও এমপ্লয়ি ফিল্টার
    # ----------------------------
    user_company = getattr(user.profile, 'company', None)
    if not user_company:
        # Company নেই
        return render(request, 'dashboard.html', {
            'error_message': "আপনার প্রোফাইলে কোম্পানি সেট করা নেই।"
        })

    today = localdate()
    selected_dept = request.GET.get('department')

    employees = Employee.objects.filter(department__company=user_company)
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)

    departments = Department.objects.filter(company=user_company)
    employee_data = []

    # ----------------------------
    # ডেলি অ্যাটেন্ডেন্স
    # ----------------------------
    for emp in employees:
        attendances = Attendance.objects.filter(employee=emp, timestamp__date=today).order_by('timestamp')
        in_times = [att.timestamp for att in attendances if att.status == 'In']
        out_times = [att.timestamp for att in attendances if att.status == 'Out']

        total_work_time = timedelta()
        for i in range(min(len(in_times), len(out_times))):
            if out_times[i] > in_times[i]:
                total_work_time += (out_times[i] - in_times[i])

        regular_work_time = timedelta(hours=10)
        late_time = over_time = less_time = timedelta()

        first_in = localtime(in_times[0]) if in_times else None
        last_out = localtime(out_times[-1]) if out_times else None

        if first_in:
            expected_start = first_in.replace(hour=10, minute=30, second=0, microsecond=0)
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

    # ----------------------------
    # ৩০ দিনের অ্যাটেন্ডেন্স ট্রেন্ড
    # ----------------------------
    start_date = today - timedelta(days=29)
    attendance_trend = []

    for i in range(30):
        date = start_date + timedelta(days=i)
        emp_ids = employees.values_list('id', flat=True)

        present_count = Attendance.objects.filter(
            employee_id__in=emp_ids,
            timestamp__date=date,
            status='In'
        ).values('employee').distinct().count()

        total_employees = employees.count()
        absent_count = total_employees - present_count

        late_count = Attendance.objects.filter(
            employee_id__in=emp_ids,
            timestamp__date=date,
            status='In',
            timestamp__time__gt='10:30:00'
        ).count()

        attendance_trend.append({
            'date': date.strftime('%Y-%m-%d'),
            'present': present_count,
            'absent': absent_count,
            'late': late_count
        })

    # ----------------------------
    # কন্টেক্সট ডিকশনারি
    # ----------------------------
    context = {
        'employee_data': employee_data,
        'departments': departments,
        'selected_department': int(selected_dept) if selected_dept else None,
        'total_employees': employees.count(),
        'present': sum(1 for emp in employee_data if emp['in_time']),
        'absent': sum(1 for emp in employee_data if not emp['in_time']),
        'late': sum(1 for emp in employee_data if emp['late_time'].total_seconds() > 0),
        'attendance_trend': attendance_trend,
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

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from .models import Department
import logging

logger = logging.getLogger(__name__)

@login_required
def sync_attendance_view(request):
    # ইউজারের প্রোফাইল থেকে কোম্পানি নাও
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই। সিস্টেম অ্যাডমিনের সাথে যোগাযোগ করুন।")
        return redirect('dashboard')

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
            return redirect('sync_attendance')

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

# ---------------Monthly Report---------------
@login_required
def monthly_work_time_report(request):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        return render(request, 'error.html', {'message': "আপনার কোম্পানি সেট করা নেই।"})

    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    selected_dept = request.GET.get('department')
    selected_emp = request.GET.get('employee')

    # ডেট রেঞ্জ সেট
    today = datetime.today().date()
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today - timedelta(days=30)
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today

    # Employees filter user company, selected dept & employee অনুযায়ী
    employees = Employee.objects.filter(department__company=user_company)
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)
    if selected_emp:
        employees = employees.filter(id=selected_emp)

    # Holidays সেট
    holidays = Holiday.objects.filter(start_date__lte=end_date, end_date__gte=start_date)
    holiday_dates = {
        holiday.start_date + timedelta(days=i)
        for holiday in holidays
        for i in range((min(holiday.end_date, end_date) - max(holiday.start_date, start_date)).days + 1)
    }

    regular_work_time = timedelta(hours=10)
    expected_start_time = time(10, 30)
    report_data = []

    for emp in employees:
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

            records = daily_attendance.get(current_date, [])
            in_times = [r.timestamp for r in records if r.status == 'In']
            out_times = [r.timestamp for r in records if r.status == 'Out']

            if current_date in leave_dates:
                leave_days_count += 1
                # Leave day হলে regular_work_time যোগ করা
                total_work_time += regular_work_time
                continue

            if in_times:
                in_time = min(in_times)
                out_time = max(out_times) if out_times else None

                if is_naive(in_time):
                    in_time = make_aware(in_time)
                if out_time and is_naive(out_time):
                    out_time = make_aware(out_time)

                adjusted_in_time = datetime.combine(in_time.date(), expected_start_time)
                if is_naive(adjusted_in_time):
                    adjusted_in_time = make_aware(adjusted_in_time)

                actual_in_time = max(in_time, adjusted_in_time)
                present_days += 1

                if out_time and out_time > actual_in_time:
                    duration = out_time - actual_in_time
                    total_work_time += duration

                    if in_time > adjusted_in_time:
                        total_late_time += in_time - adjusted_in_time

                    if duration > regular_work_time:
                        total_over_time += duration - regular_work_time

        expected_work_time = present_days * regular_work_time
        total_less_time = max(expected_work_time - total_work_time, timedelta())
        early_leave_time = max(total_less_time - total_late_time, timedelta())

        absent_days = total_days - present_days - weekly_off_count - leave_days_count - holiday_count
        expected_work_time_excl_off = (total_days - weekly_off_count - leave_days_count - holiday_count) * regular_work_time
        work_time_difference = expected_work_time_excl_off - total_work_time
        final_work_time = total_work_time

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
            'final_work_time': format_timedelta(final_work_time),
            'present_days': present_days,
            'absent_days': absent_days,
            'weekly_off_days': weekly_off_count,
            'leave_days': leave_days_count,
            'holiday_days': holiday_count,
            'approved_leave_count': approved_leaves.count(),
            'start_date': start_date,
            'end_date': end_date,
            'expected_work_time_excl_off': expected_work_time_excl_off,
            'work_time_difference': work_time_difference,
        })

    # Departments & Employees list for filter dropdowns
    departments = Department.objects.filter(company=user_company)
    employees_all = Employee.objects.filter(department__company=user_company)
    if selected_dept:
        employees_all = employees_all.filter(department__id=selected_dept)

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

# --------------montly work time PDF---------------
from django.contrib.auth.decorators import login_required
from django.shortcuts import HttpResponse
from django.utils.timezone import make_aware, is_naive
from collections import defaultdict
from datetime import datetime, timedelta
from django.template.loader import render_to_string
from weasyprint import HTML
from .models import Employee, Department, Attendance, LeaveRequest
@login_required
def monthly_work_time_pdf(request):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        return HttpResponse("আপনার কোম্পানি সেট করা নেই।", status=400)

    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    selected_dept = request.GET.get('department')
    selected_emp = request.GET.get('employee')

    today = datetime.today().date()
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else today - timedelta(days=30)
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else today
    except ValueError:
        return HttpResponse("Invalid date format. Use YYYY-MM-DD", status=400)

    # Employees filter by user company + selected dept/employee
    employees = Employee.objects.filter(department__company=user_company)
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)
    if selected_emp:
        employees = employees.filter(id=selected_emp)

    report_data = []
    regular_work_time = timedelta(hours=10)
    expected_start_time = datetime.strptime("10:30", "%H:%M").time()

    for emp in employees:
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

        leave_dates = set()
        for leave in approved_leaves:
            leave_start = max(leave.start_date, start_date)
            leave_end = min(leave.end_date, end_date)
            for n in range((leave_end - leave_start).days + 1):
                leave_dates.add(leave_start + timedelta(days=n))

        daily_attendance = defaultdict(list)
        for att in attendances:
            daily_attendance[att.timestamp.date()].append(att)

        total_work_time = timedelta()
        total_late_time = timedelta()
        total_over_time = timedelta()
        total_less_time = timedelta()
        weekly_off_day = emp.department.weekly_off_day if emp.department else None

        days_in_range = (end_date - start_date).days + 1
        present_days = 0
        weekly_off_count = 0
        leave_day_count = 0

        for n in range(days_in_range):
            current_date = start_date + timedelta(days=n)
            weekday = current_date.strftime('%A')

            if weekly_off_day and weekday == weekly_off_day:
                weekly_off_count += 1
                continue

            if current_date in leave_dates:
                leave_day_count += 1
                # Leave দিনগুলোকে 10 ঘণ্টা ধরে total_work_time-এ যোগ করা
                total_work_time += regular_work_time
                continue

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

                expected_start_dt = make_aware(datetime.combine(current_date, expected_start_time))
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

        total_excluded_days = weekly_off_count + leave_day_count
        absent_days = days_in_range - present_days - total_excluded_days
        total_work_days_excl_off_leave = days_in_range - total_excluded_days
        expected_work_time_excl_off_leave = total_work_days_excl_off_leave * regular_work_time
        work_time_difference = expected_work_time_excl_off_leave - total_work_time

        report_data.append({
            'employee': emp,
            'present_days': present_days,
            'absent_days': absent_days,
            'weekly_off_days': weekly_off_count,
            'leave_days': leave_day_count,
            'total_work_hours': total_work_time,
            'expected_work_hours': expected_work_time_excl_off_leave,
            'difference_time': work_time_difference,
            'late_hours': total_late_time,
            'over_hours': total_over_time,
            'less_hours': total_less_time,
        })

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
        return redirect('dashboard')

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
            return redirect('employee_list')
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
            return redirect('employee_list')
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
        return redirect('employee_list')

    return render(request, 'employee_confirm_delete.html', {'employee': employee})



# ----------------Deperment -------------------
# ----------------------------
# Department List
# ----------------------------
@login_required
def department_list(request):
    # শুধুমাত্র current user's company-এর department দেখাবে
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        departments = Department.objects.none()
    else:
        departments = Department.objects.filter(company=user_company)

    return render(request, 'department_list.html', {'departments': departments})


# ----------------------------
# Department Add/Edit
# ----------------------------
@login_required
def department_form_view(request, pk=None):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('department_list')

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
            department.company = user_company  # ইউজারের কোম্পানি সেট করা
            department.save()
            messages.success(request, f"Department {'updated' if pk else 'added'} successfully.")
            return redirect('department_list')
    else:
        form = DepartmentForm(instance=dept)

    return render(request, 'department_form.html', {
        'form': form,
        'title': title,
    })


# ----------------------------
# Department Delete
# ----------------------------
@login_required
def department_delete(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    dept = get_object_or_404(Department, pk=pk, company=user_company)
    dept.delete()
    messages.success(request, "Department deleted successfully.")
    return redirect('department_list')


# ---------------------Attendance---------
# views.py
from attendance_app.utils.attendance_helpers import generate_attendance_table

from django.db.models import Min, Max
from .models import Attendance, Employee

from datetime import date
from django.utils.dateparse import parse_date

from collections import defaultdict
from datetime import datetime, timedelta, time, date
from django.utils.timezone import make_aware
from django.shortcuts import render
from .models import Employee, Attendance, Department, Holiday, LeaveRequest

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
# Generate Attendance Table
# ----------------------------
def generate_attendance_table(employees, start_date, end_date):
    summary = []
    start = datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.strptime(end_date, '%Y-%m-%d').date()
    holidays = Holiday.objects.all()
    leave_requests = LeaveRequest.objects.filter(status='Approved')
    standard_in_time = time(10, 30)

    for emp in employees:
        department = getattr(emp, 'department', None)
        weekly_off_day_str = department.weekly_off_day if department else 'Friday'
        weekly_off_day = WEEKDAY_MAP.get(weekly_off_day_str, 4)

        attendance_records = Attendance.objects.filter(
            employee=emp,
            timestamp__date__range=(start, end)
        ).order_by('timestamp')

        daily_records = defaultdict(list)
        for record in attendance_records:
            daily_records[record.timestamp.date()].append(record)

        current = start
        while current <= end:
            records = daily_records.get(current, [])
            in_time = None
            out_time = None
            attendance_id = None

            # Check public holiday, weekly off, leave
            is_public_holiday = holidays.filter(start_date__lte=current, end_date__gte=current).exists()
            is_weekly_off = current.weekday() == weekly_off_day
            is_on_leave = leave_requests.filter(employee=emp, start_date__lte=current, end_date__gte=current).exists()

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
                    earliest_in_record = min(ins, key=lambda r: r.timestamp)
                    attendance_id = earliest_in_record.id
                    standard_in_datetime = make_aware(datetime.combine(current, standard_in_time))
                    in_time = max(earliest_in_record.timestamp, standard_in_datetime)

                if outs:
                    latest_out_record = max(outs, key=lambda r: r.timestamp)
                    out_time = latest_out_record.timestamp

                status = 'Present' if in_time or out_time else 'Absent'
            else:
                status = 'Absent'

            summary.append({
                'employee': emp,
                'date': current,
                'in_time': in_time,
                'out_time': out_time,
                'status': status,
                'id': attendance_id
            })

            current += timedelta(days=1)

    return summary
from django.contrib.auth.decorators import login_required
from datetime import date
from .models import Employee, Department, Attendance
from .forms import AttendanceForm

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
def attendance_add(request):
    user_company = getattr(request.user.profile, 'company', None)
    
    if request.method == 'POST':
        form = AttendanceForm(request.POST)
        if form.is_valid():
            attendance = form.save(commit=False)
            attendance.user = request.user
            attendance.save()
            return redirect('attendance_list')
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


# ----------------------------
# Delete Attendance
# ----------------------------
@login_required
def attendance_delete(request, pk):
    attendance = get_object_or_404(Attendance, pk=pk, user=request.user)  # শুধু নিজের ইউজারের ডাটা
    if request.method == 'POST':
        attendance.delete()
        return redirect('attendance_list')
    return render(request, 'attendance_confirm_delete.html', {
        'attendance': attendance
    })


# --------------emplyee details----------------


# --------------emplyee details----------------


from django.shortcuts import render, get_object_or_404
from django.utils.timezone import make_aware, is_naive

from .models import Employee, Attendance, LeaveRequest, Holiday
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta
import calendar


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
        public_holidays = Holiday.objects.none()  # company না থাকলে কিছু দেখাবে না

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

    for att_date, timestamps in daily_attendance.items():
        timestamps.sort()
        in_time = timestamps[0]
        out_time = timestamps[-1] if len(timestamps) > 1 else None

        summary[att_date]['in_time'] = in_time
        summary[att_date]['out_time'] = out_time
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
    ten_hours = timedelta(hours=10)
    expected_start_time = datetime.strptime("10:30", "%H:%M").time()

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
            total_work_duration += ten_hours

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

            # Compare with 10 hours
            if daily_work_time > standard_work_duration:
                over_time = daily_work_time - standard_work_duration
                less_time = timedelta()
            else:
                less_time = standard_work_duration - daily_work_time
                over_time = timedelta()
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

# views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from .models import LeaveRequest, Employee
from .forms import LeaveRequestForm

@login_required
def leave_list(request):
    # শুধু সেই leaves দেখাবে যেগুলো current user's company বা department এর
    user_company = getattr(request.user.profile, 'company', None)
    if user_company:
        leaves = LeaveRequest.objects.select_related('employee').filter(employee__company=user_company)
    else:
        leaves = LeaveRequest.objects.none()  # যদি company না থাকে, খালি queryset
    return render(request, 'leave_list.html', {'leaves': leaves})


@login_required
def leave_create(request):
    form = LeaveRequestForm(request.POST or None, user=request.user)  # user পাস করছি form এ
    if form.is_valid():
        leave = form.save(commit=False)
        leave.approved_by = request.user
        leave.save()
        return redirect('leave_list')
    return render(request, 'leave_form.html', {'form': form, 'title': 'Create Leave'})


@login_required
def leave_update(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    form = LeaveRequestForm(request.POST or None, instance=leave, user=request.user)
    if form.is_valid():
        form.save()
        return redirect('leave_list')
    return render(request, 'leave_form.html', {'form': form, 'title': 'Update Leave'})


@login_required
def leave_delete(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    leave.delete()
    return redirect('leave_list')


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
            return redirect('holiday_list')
        else:
            messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
            return redirect('holiday_list')
    return render(request, 'holiday_form.html', {'form': form, 'title': 'Add Holiday'})


@login_required
def holiday_edit(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('dashboard')

    holiday = get_object_or_404(Holiday, pk=pk, company=user_company)
    form = HolidayForm(request.POST or None, instance=holiday)
    if form.is_valid():
        form.save()
        return redirect('holiday_list')

    return render(request, 'holiday_form.html', {'form': form, 'title': 'Edit Holiday'})

@login_required
def holiday_delete(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('dashboard')

    holiday = get_object_or_404(Holiday, pk=pk, company=user_company)
    if request.method == 'POST':
        holiday.delete()
        return redirect('holiday_list')

    return render(request, 'holiday_confirm_delete.html', {'holiday': holiday})
