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
from django.db import transaction
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
from .models import Employee, Department, Attendance, Holiday, LeaveRequest, EmployeeTransfer
from . import scoping
from attendance_app.utils.zk_import import import_attendance, import_pushed
from attendance_app.utils.attendance_helpers import generate_attendance_table
from attendance_app.utils import attendance_helpers as att_rules
from attendance_app.utils.params import int_param, date_param
from attendance_app.utils.pdf import render_pdf
from subscription_app.models import UserSubscription
from subscription_app.decorators import subscription_required
from subscription_app.utils import is_subscription_active

from django.utils.safestring import mark_safe

# ---------- Dashboard (Optimized) ----------

@login_required
def dashboard(request):
    user = request.user

    # 1) সাবস্ক্রিপশন গার্ড (হার্ড ব্লক)
    if not is_subscription_active(user):
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
    selected_dept = int_param(request.GET, 'department')

    # ব্রাঞ্চ: ম্যানেজার হলে নিজের ব্রাঞ্চ, মালিক হলে সুইচারে বাছাই করা ব্রাঞ্চ বা সব
    scope = scoping.get_scope(request)
    employees = scoping.employees(Employee.objects.select_related('department', 'department__branch'), scope)
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)
    employees = list(employees)
    total_employees = len(employees)

    departments = scoping.departments(Department.objects.all(), scope)

    # 3) ৩০ দিনের হিসাব একবারে (আজকের দিনটা শেষ দিন) — একই নিয়ম সব পেজে
    start_date = today - timedelta(days=29)
    per_emp = att_rules.build_attendance(employees, start_date, today, branch_id=scope.branch_id)

    employee_data = []
    for emp, _shift, days in per_emp:
        d = days[-1]  # আজ
        if d['in_time'] and d['out_time']:
            status_display = "Present"
        elif d['in_time']:
            status_display = "Present (Active)"  # মাত্র এসেছে, বা আউট দিতে ভুলে গেছে
        else:
            status_display = "Absent"

        employee_data.append({
            'employee': emp,
            'in_time': d['in_time'].time() if d['in_time'] else None,
            'out_time': d['out_time'].time() if d['out_time'] else None,
            'total_work_time': d['work'],
            'late_time': d['late'],
            'over_time': d['over'],
            # দিন শেষ না হলে (Out নেই) কম সময় দেখাই না
            'less_time': d['less'] if d['out_time'] else timedelta(),
            'status': status_display,
        })

    # 4) ৩০ দিনের ট্রেন্ড
    attendance_trend = []
    for i in range(30):
        present = sum(1 for _e, _s, days in per_emp if days[i]['status'] == 'Present')
        late = sum(1 for _e, _s, days in per_emp if days[i]['late'] > timedelta())
        attendance_trend.append({
            'date': (start_date + timedelta(days=i)).strftime('%Y-%m-%d'),
            'present': present,
            'absent': total_employees - present,  # Total - Present = Absent
            'late': late,
        })

    # 5) সব ব্রাঞ্চ দেখার সময়: ব্রাঞ্চ অনুযায়ী আজকের তুলনা
    branch_stats = []
    if not scope.branch:
        by_branch = {}
        for row in employee_data:
            dept = row['employee'].department
            b = dept.branch if dept and dept.branch_id else None
            key = b.id if b else 0
            st = by_branch.setdefault(key, {'branch': b, 'total': 0, 'present': 0, 'late': 0})
            st['total'] += 1
            st['present'] += 1 if row['in_time'] else 0
            st['late'] += 1 if row['late_time'].total_seconds() > 0 else 0
        branch_stats = sorted(by_branch.values(), key=lambda x: (x['branch'] is None, x['branch'].name if x['branch'] else ''))
        for st in branch_stats:
            st['absent'] = st['total'] - st['present']
            st['rate'] = round(100 * st['present'] / st['total']) if st['total'] else 0
        if len(branch_stats) < 2:
            branch_stats = []

    # 6) কন্টেক্সট
    context = {
        'branch_stats': branch_stats,
        'employee_data': employee_data,
        'departments': departments,
        'selected_department': selected_dept,
        'total_employees': total_employees,
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
# লগইন/সাবস্ক্রিপশন গার্ডের বাইরে (root urls.py তে)। ডিভাইস নিজেকে চেনায় ডিপার্টমেন্টের push_token দিয়ে:
#   Header  X-Device-Token: <token>   অথবা   ?token=<token>
# Body (push_agent.py): {"users": [{"uid": 12, "name": "..."}], "punches": [{"uid": 12, "time": "2026-10-25 09:31:00"}]}
#   — মেশিন সিঙ্কের মতোই: নতুন কর্মচারী যোগ, দিনে প্রথম পাঞ্চ In / শেষ পাঞ্চ Out
# পুরনো ফরম্যাট (শুধু লিস্ট) এখনো চলে: [{"uid": 12, "time": "2026-10-25 09:31:00"}, ...]
@csrf_exempt
def zkteco_push_view(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'invalid request'}, status=405)

    token = request.headers.get('X-Device-Token') or request.GET.get('token')
    if not token:
        return JsonResponse({'status': 'error', 'message': 'Device token required'}, status=401)
    department = Department.objects.select_related('company').filter(push_token=token).first()
    if not department or not department.company_id:
        return JsonResponse({'status': 'error', 'message': 'Unknown device token'}, status=403)
    company = department.company

    try:
        data = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    if isinstance(data, dict):
        users, punches = data.get('users') or [], data.get('punches') or []
        if not isinstance(users, list) or not isinstance(punches, list):
            return JsonResponse({'status': 'error', 'message': 'users and punches must be lists'}, status=400)
        return JsonResponse(import_pushed(department, users, punches))
    if not isinstance(data, list):
        return JsonResponse({'status': 'error', 'message': 'Expected a JSON list'}, status=400)

    # শুধু এই মেশিনের ব্রাঞ্চের employee — অন্য ব্রাঞ্চ/কোম্পানিতে একই device_user_id থাকলেও সমস্যা নেই
    emp_by_uid = {e.device_user_id: e for e in Employee.id_scope(department)}

    to_create, skipped = [], 0
    for entry in data:
        try:
            uid = int(entry.get('uid'))
            timestamp = make_aware(datetime.strptime(entry.get('time'), "%Y-%m-%d %H:%M:%S"))
        except (AttributeError, TypeError, ValueError):
            skipped += 1
            continue
        emp = emp_by_uid.get(uid)
        if not emp:
            skipped += 1
            continue
        status = 'In' if timestamp.hour < 13 else 'Out'
        to_create.append(Attendance(employee=emp, company=company, timestamp=timestamp, status=status))

    # একই পাঞ্চ আবার এলে unique constraint এ আটকে যায়, ডুপ্লিকেট হয় না
    Attendance.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)
    return JsonResponse({'status': 'success', 'received': len(data), 'accepted': len(to_create), 'skipped': skipped})


# 📌 Manual Sync Button with Department Filter


logger = logging.getLogger(__name__)

@login_required
def sync_attendance_view(request):
    # ইউজারের প্রোফাইল থেকে কোম্পানি নাও
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই। সিস্টেম অ্যাডমিনের সাথে যোগাযোগ করুন।")
        return redirect('attendance_app:dashboard')

    # ওই কোম্পানির (ম্যানেজার হলে নিজের ব্রাঞ্চের) ডিপার্টমেন্টগুলো নাও
    departments = scoping.departments(Department.objects.select_related('branch'), scoping.get_scope(request))

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
            return redirect('attendance_app:sync_attendance')

        # ডিপার্টমেন্ট মডেল থেকে IP ও Port নেওয়া
        if not department.device_ip or not department.device_port:
            error_msg = f"{department.name} ডিপার্টমেন্টের জন্য ডিভাইস IP/Port সেট করা নেই।"
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                from django.http import JsonResponse
                return JsonResponse({'status': 'error', 'message': error_msg})
            messages.error(request, error_msg)
            return redirect('attendance_app:sync_attendance')

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

        return redirect('attendance_app:employee_list')

    return render(request, 'sync_form.html', {'departments': departments})

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
        start_date = date_param(request.GET, 'start_date')
        end_date = date_param(request.GET, 'end_date')

    selected_dept = int_param(request.GET, 'department')
    selected_emp = int_param(request.GET, 'employee')

    # ব্রাঞ্চ রিপোর্ট: এই সময়ে ব্রাঞ্চে ছিল এমন সবাই (বদলির ইতিহাসসহ); অন্য ব্রাঞ্চের দিনগুলো বাদ
    scope = scoping.get_scope(request)
    employees = scoping.employees_for_period(
        Employee.objects.select_related('department', 'department__branch'), scope, start_date, end_date)
    if selected_dept:
        employees = employees.filter(department__id=selected_dept)
    if selected_emp:
        employees = employees.filter(id=selected_emp)

    fmt = att_rules.format_timedelta
    report_data = []
    for emp, shift, days in att_rules.build_attendance(employees, start_date, end_date, branch_id=scope.branch_id):
        if all(d['is_elsewhere'] for d in days):
            continue
        s = att_rules.summarize(days, shift)
        report_data.append({
            'employee': emp, 'present_days': s['present'], 'absent_days': s['absent'],
            'weekly_off_days': s['weekly_off'], 'leave_days': s['leave'], 'holiday_days': s['holiday'],
            'total_work_hours': fmt(s['total_work']), 'late_time': fmt(s['total_late']),
            'over_time': fmt(s['total_over']), 'less_time': fmt(s['total_less']),
            'expected_work_time_excl_off': s['expected'], 'work_time_difference': s['difference'],
            'start_date': start_date, 'end_date': end_date
        })

    return {
        'report_data': report_data,
        'start_date': start_date, 'end_date': end_date,
        'selected_department': selected_dept, 'selected_employee': selected_emp,
        'departments': scoping.departments(Department.objects.all(), scope),
        'employees': scoping.employees(Employee.objects.all(), scope),
        'company_name': user_company.name if user_company else "Company",
        'branch_name': scope.branch.name if scope.branch else '',
    }


@login_required
def monthly_work_time_report(request):
    context = get_monthly_report_context(request)
    return render(request, 'monthly_report.html', context)

@login_required
def monthly_work_time_pdf(request):
    context = get_monthly_report_context(request)
    rows = context['report_data']
    totals = {key: sum(r[field] for r in rows) for key, field in (
        ('present', 'present_days'), ('absent', 'absent_days'), ('leave', 'leave_days'),
        ('weekly_off', 'weekly_off_days'), ('holiday', 'holiday_days'))}
    totals['off'] = totals['weekly_off'] + totals['holiday']
    counted = totals['present'] + totals['absent']
    totals['attendance_rate'] = round(totals['present'] * 100 / counted) if counted else 0
    start, end = context['start_date'], context['end_date']
    context.update({
        'totals': totals,
        'period_days': (end - start).days + 1,
        'department_name': _name_of(context['departments'], context['selected_department']),
        'employee_name': _name_of(context['employees'], context['selected_employee']),
    })
    return render_pdf(request, 'monthly_work_time_report_pdf.html', context, f"Monthly_Report_{start}_to_{end}.pdf")


def _name_of(qs, pk):
    """ফিল্টারের id থেকে নাম (PDF হেডারের জন্য); না থাকলে ফাঁকা"""
    return (qs.filter(pk=pk).values_list('name', flat=True).first() or '') if pk else ''



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
    employees = scoping.employees(Employee.objects.select_related('department', 'department__branch'), scoping.get_scope(request))

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
        return redirect('attendance_app:employee_list')

    if request.method == 'POST':
        form = EmployeeForm(request.POST, user=request.user)
        if form.is_valid():
            employee = form.save(commit=False)
            employee.company = user_company  # কোম্পানি সেট করা
            employee.save()
            messages.success(request, "Employee সফলভাবে যোগ হয়েছে।")
            return redirect('attendance_app:employee_list')
    else:
        form = EmployeeForm(user=request.user)

    return render(request, 'employee_form.html', {'form': form, 'title': 'Add Employee'})


# Employee Edit View
@login_required
def employee_edit(request, pk):
    scope = scoping.get_scope(request)
    employee = get_object_or_404(scoping.employees(Employee.objects.select_related('department'), scope.security), pk=pk)
    old_department = employee.department

    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=employee, user=request.user)
        if form.is_valid():
            employee = form.save()
            # অন্য ব্রাঞ্চের ডিপার্টমেন্টে সরালে আজ থেকে কার্যকর বদলি হিসেবে ইতিহাসে রাখি
            old_branch_id = old_department.branch_id if old_department else None
            new_branch_id = employee.department.branch_id if employee.department_id else None
            if old_branch_id != new_branch_id:
                EmployeeTransfer.objects.create(
                    employee=employee, from_branch_id=old_branch_id, to_branch_id=new_branch_id,
                    from_department=old_department, to_department=employee.department,
                    effective_date=localdate(), note="Department changed from the edit form", created_by=request.user,
                )
            messages.success(request, "Employee তথ্য আপডেট হয়েছে।")
            return redirect('attendance_app:employee_list')
    else:
        form = EmployeeForm(instance=employee, user=request.user)

    return render(request, 'employee_form.html', {'form': form, 'title': 'Edit Employee'})


# Employee Delete View
@login_required
def employee_delete(request, pk):
    employee = get_object_or_404(scoping.employees(Employee.objects.all(), scoping.get_scope(request).security), pk=pk)

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
        return redirect("attendance_app:dashboard")  # বা যেকোনো সেফ রুট

    # base queryset: শুধুই ইউজারের কোম্পানির ডিপার্টমেন্ট
    qs = scoping.departments(Department.objects.select_related("company", "branch"), scoping.get_scope(request)).order_by("branch__name", "name")

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
        dept = get_object_or_404(scoping.departments(Department.objects.all(), scoping.get_scope(request).security), pk=pk)
        title = 'Edit Department'
    else:
        dept = None
        title = 'Add Department'

    if request.method == 'POST':
        form = DepartmentForm(request.POST, instance=dept, user=request.user)
        if form.is_valid():
            department = form.save(commit=False)
            department.company = user_company  # user-এর কোম্পানি ফোর্স করা
            department.save()
            messages.success(request, f"Department {'updated' if pk else 'added'} successfully.")
            return redirect('attendance_app:department_list')
    else:
        form = DepartmentForm(instance=dept, user=request.user)

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
    dept = get_object_or_404(scoping.departments(Department.objects.all(), scoping.get_scope(request).security), pk=pk)
    name = dept.name  # শুধু মেসেজে দেখানোর জন্য আগে রেখে দিলাম
    dept.delete()
    messages.success(request, f"Department '{name}' deleted successfully.")
    return redirect("attendance_app:department_list")

# ---------------------Attendance---------

@login_required
def attendance_list(request):
    user_company = getattr(request.user.profile, 'company', None)
    
    # 1. Company Check
    if not user_company:
        messages.error(request, "Company not found.")
        employees = Employee.objects.none()
    scope = scoping.get_scope(request)
    start_d = date_param(request.GET, 'start_date', localdate())
    end_d = date_param(request.GET, 'end_date', localdate())
    if user_company:
        employees = scoping.employees_for_period(
            Employee.objects.select_related('department', 'department__branch'), scope, start_d, end_d)

    # 2. Filter Inputs
    start_date_str = start_d.strftime('%Y-%m-%d')
    end_date_str = end_d.strftime('%Y-%m-%d')
    
    emp_id = int_param(request.GET, 'employee')
    dept_id = int_param(request.GET, 'department')

    # 3. Apply Filters
    if emp_id:
        employees = employees.filter(id=emp_id)
    if dept_id:
        employees = employees.filter(department_id=dept_id)

    # 4. Generate Data (Using Optimized Helper)
    attendance_summary = generate_attendance_table(employees, start_date_str, end_date_str, branch_id=scope.branch_id)

    # 5. Context Data
    departments = scoping.departments(Department.objects.all(), scope) if user_company else []

    # এক মাসে সবার সারি হাজার হাজার হয় — পেজে ১০০টা করে (PDF এ সব থাকে)
    page_obj = Paginator(attendance_summary, 100).get_page(request.GET.get('page'))

    return render(request, 'attendance_list.html', {
        'attendance_summary': page_obj,
        'page_obj': page_obj,
        'employees': employees, # For filter persistence if needed (optional)
        'all_employees': scoping.employees(Employee.objects.all(), scope) if user_company else [],  # ড্রপডাউন
        'departments': departments,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'selected_employee': emp_id,
        'selected_department': dept_id,
    })


# -------------------------------------------------------------------------
# ✅ Optimized Attendance List PDF (Consistent with Report View)
# -------------------------------------------------------------------------
@login_required
def attendance_list_pdf(request):
    """Attendance -> PDF (inline). HTML পেজের একই হেল্পার, তাই সংখ্যা সব জায়গায় এক।"""
    if not getattr(request.user.profile, 'company', None):
        return HttpResponseForbidden("Company not set.")

    start = date_param(request.GET, 'start_date', localdate())
    end = date_param(request.GET, 'end_date', localdate())
    employee_id = int_param(request.GET, 'employee')
    department_id = int_param(request.GET, 'department')

    # ব্রাঞ্চ + বদলির ইতিহাসসহ, HTML ভিউর মতোই
    scope = scoping.get_scope(request)
    employees = scoping.employees_for_period(
        Employee.objects.select_related('department', 'department__branch'), scope, start, end)
    if employee_id:
        employees = employees.filter(id=employee_id)
    if department_id:
        employees = employees.filter(department__id=department_id)

    common = {
        'start_date': start, 'end_date': end,
        'department_name': _name_of(scoping.departments(Department.objects.all(), scope), department_id),
        'employee_name': _name_of(scoping.employees(Employee.objects.all(), scope), employee_id),
    }
    # একাধিক দিন: হাজিরা খাতা (কর্মচারী × তারিখ) — পড়তে সহজ, আর হাজার সারির লিস্টের চেয়ে অনেক দ্রুত
    if start != end and request.GET.get('layout') != 'list':
        context = {**common, **_attendance_register(employees, start, end, scope.branch_id)}
        return render_pdf(request, 'attendance_register_pdf.html', context, f"Attendance_Register_{start}_to_{end}.pdf")

    # এক দিন (বা layout=list): প্রতিটা সারিতে In/Out — দিন ধরে গ্রুপ
    days, counts, total_work = [], defaultdict(int), timedelta()
    for r in generate_attendance_table(employees, start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'), branch_id=scope.branch_id):
        if not days or days[-1]['date'] != r['date']:
            days.append({'date': r['date'], 'rows': [], 'present': 0})
        work = r['work'] if r['in_time'] and r['out_time'] else None
        if work:
            total_work += work
        days[-1]['rows'].append({
            'employee': r['employee'],
            'in_time': r['in_time'], 'out_time': r['out_time'],
            'worked': att_rules.format_timedelta(work)[:5] if work else '',
            'status': r['status'],
        })
        counts[r['status']] += 1
        if r['status'] == 'Present':
            days[-1]['present'] += 1

    context = {
        **common,
        'days': days,
        'record_count': sum(counts.values()),
        'present': counts['Present'], 'absent': counts['Absent'], 'leave': counts['Leave'],
        'off': counts['Weekly Off'] + counts['Public Holiday'],
        'total_worked': _hhmm(total_work),
    }
    return render_pdf(request, 'attendance_list_pdf.html', context, f"Attendance_{start}_to_{end}.pdf")


def _hhmm(td):
    hours, rest = divmod(int(td.total_seconds()), 3600)
    return f"{hours:,}:{rest // 60:02}"


# হাজিরা খাতার কোড: (অক্ষর, রঙের ক্লাস)
_REGISTER_CODES = {
    'Absent': ('A', 'c-a'), 'Leave': ('L', 'c-l'), 'Public Holiday': ('H', 'c-h'),
    'Weekly Off': ('W', 'c-w'), 'Other Branch': ('·', 'c-x'),
}
REGISTER_BLOCK_DAYS = 31   # এক টেবিলে সর্বোচ্চ এত দিন (A4 landscape এ আঁটে)


def _attendance_register(employees, start, end, branch_id):
    """কর্মচারী × তারিখ ম্যাট্রিক্স, ৩১ দিনের ব্লকে ভাগ করা; প্রতিটা ব্লকের নিজস্ব যোগফল"""
    per_emp = [(emp, shift, days) for emp, shift, days in att_rules.build_attendance(employees, start, end, branch_id=branch_id)
               if not all(d['is_elsewhere'] for d in days)]
    dates = list(att_rules.date_range(start, end))
    blocks, grand = [], defaultdict(int)
    for i in range(0, len(dates), REGISTER_BLOCK_DAYS):
        block_dates = dates[i:i + REGISTER_BLOCK_DAYS]
        rows = []
        for emp, shift, days in per_emp:
            part = days[i:i + REGISTER_BLOCK_DAYS]
            cells = []
            for d in part:
                if d['status'] == 'Present':
                    # দেরিতে এলে P অ্যাম্বার, ছুটির দিনে কাজ করলে P বেগুনি
                    cells.append(('P', 'c-pl' if d['late'] else 'c-ph' if (d['is_holiday'] or d['is_off_day']) else 'c-p'))
                else:
                    cells.append(_REGISTER_CODES.get(d['status'], ('?', 'c-x')))
            sm = att_rules.summarize(part, shift)
            late = sum(1 for d in part if d['late'])
            rows.append({'employee': emp, 'cells': cells, 'present': sm['present'], 'late': late,
                         'absent': sm['absent'], 'leave': sm['leave'], 'off': sm['holiday'] + sm['weekly_off'],
                         'worked': _hhmm(sm['total_work'])})
            for k in ('present', 'late', 'absent', 'leave', 'off'):
                grand[k] += rows[-1][k]
        blocks.append({'dates': block_dates, 'rows': rows})
    counted = grand['present'] + grand['absent']
    return {
        'blocks': blocks, 'employee_count': len(per_emp), 'period_days': len(dates),
        'totals': dict(grand), 'attendance_rate': round(grand['present'] * 100 / counted) if counted else 0,
    }



@login_required
def attendance_add(request):
    user_company = getattr(request.user.profile, 'company', None)
    
    # form নিজেই employees কে user-এর company তে সীমাবদ্ধ করে (GET ও POST দুটোতেই)
    if request.method == 'POST':
        form = AttendanceForm(request.POST, user=request.user)
        if form.is_valid():
            attendance = form.save(commit=False)
            attendance.company = user_company
            attendance.save()
            return redirect('attendance_app:attendance_list')
    else:
        form = AttendanceForm(user=request.user)

    return render(request, 'attendance_form.html', {
        'form': form,
        'title': 'Add Attendance'
    })



from django.utils.timezone import is_aware, make_naive, get_current_timezone

def to_dt_local_str(dt):
    """HTML datetime-local এর জন্য YYYY-MM-DDTHH:MM স্ট্রিং বানায়"""
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
        scoping.attendance(Attendance.objects.select_related('employee'), scoping.get_scope(request).security),
        pk=pk,
    )
    emp = anchor.employee
    day = att_rules.local_date(anchor.timestamp)  # UTC নয়, লোকাল তারিখ

    # ওই দিনের সব রেকর্ড (In/Out যাই হোক, যতগুলোই থাকুক)
    day_qs = Attendance.objects.filter(employee=emp, timestamp__date=day).order_by('timestamp')
    day_punches = list(day_qs)
    # সব পেজের মতো একই নিয়ম: প্রথম পাঞ্চ = In, শেষ পাঞ্চ = Out
    first_punch = day_punches[0] if day_punches else None
    last_punch = day_punches[-1] if len(day_punches) > 1 else None

    if request.method == 'POST':
        form = DayAttendanceForm(request.POST)
        if form.is_valid():
            in_time = form.cleaned_data['in_time']
            out_time = form.cleaned_data['out_time']

            # দিনটা পুরো নতুন করে লিখি: পুরনো সব পাঞ্চ মুছে ঠিক একটা In ও একটা Out।
            # আগে শুধু প্রথম/শেষটা আপডেট হতো, ফলে একই সময়ের আরেকটা রেকর্ড থাকলে
            # uniq_employee_timestamp_status ভেঙে IntegrityError হতো, আর বাকি পাঞ্চগুলো থেকে যেত।
            with transaction.atomic():
                day_qs.delete()
                for ts, status in ((in_time, 'In'), (out_time, 'Out')):
                    if ts:
                        # অন্য দিনে একই সময়/স্ট্যাটাসের রেকর্ড থাকলে সেটাই ব্যবহার (ডুপ্লিকেট নয়)
                        Attendance.objects.get_or_create(
                            employee=emp, timestamp=ts, status=status,
                            defaults={'company': emp.company},
                        )

            if in_time or out_time:
                messages.success(request, f"{emp.name} — {day} এর হাজিরা আপডেট হয়েছে।")
            else:
                messages.warning(request, f"{emp.name} — {day} এর সব পাঞ্চ মুছে ফেলা হয়েছে।")
            return redirect('attendance_app:attendance_list')
        in_value = request.POST.get('in_time', '')
        out_value = request.POST.get('out_time', '')
    else:
        # ❗️HTML datetime-local এর জন্য স্ট্রিং বানিয়ে টেমপ্লেটে পাঠাই
        in_value = to_dt_local_str(first_punch.timestamp if first_punch else None)
        out_value = to_dt_local_str(last_punch.timestamp if last_punch else None)
        form = DayAttendanceForm(initial={'in_time': in_value, 'out_time': out_value})

    return render(request, 'attendance_edit_form.html', {
        'form': form,
        'title': f'Edit Attendance for {emp} on {day}',
        'in_value': in_value,
        'out_value': out_value,
        'day_punches': [(att_rules.to_local(p.timestamp), p.status) for p in day_punches],
    })
# ----------------------------
# Delete Attendance
# ----------------------------
@login_required
def attendance_delete(request, pk):
    user_company = getattr(getattr(request.user, 'profile', None), 'company', None)

    # pk থেকে দিন ও employee বের করি
    anchor = get_object_or_404(
        scoping.attendance(Attendance.objects.select_related('employee'), scoping.get_scope(request).security),
        pk=pk,
    )
    day = att_rules.local_date(anchor.timestamp)  # UTC নয়, লোকাল তারিখ

    if request.method == 'POST':
        # ✅ সেই দিনের একই employee-র সব In/Out রেকর্ড ডিলিট
        Attendance.objects.filter(
            employee=anchor.employee,
            timestamp__date=day
        ).delete()
        return redirect('attendance_app:attendance_list')

    return render(request, 'attendance_confirm_delete.html', {'attendance': anchor})

from dateutil import parser  # নিশ্চিত করুন এটি ইমপোর্ট করা আছে


def _employee_report(request, employee_id):
    """
    Employee Detail (HTML) ও দুটো PDF এর কমন ডাটা।
    রিটার্ন: (employee, user_company, start_date, end_date, days, summary)
    """
    # শুধু নিজের কোম্পানির employee
    user_company = getattr(getattr(request.user, 'profile', None), 'company', None)
    employee = get_object_or_404(
        scoping.employees(Employee.objects.select_related('department', 'department__branch'), scoping.get_scope(request).security),
        id=employee_id)

    # ফ্লেক্সিবল ডেট পার্সিং (Dec 1, 2025 বা 2025-12-01)
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    today = localdate()
    try:
        if start_date_str and end_date_str:
            start_date = parser.parse(start_date_str).date()
            end_date = parser.parse(end_date_str).date()
        else:
            start_date = today.replace(day=1)
            end_date = today
    except (ValueError, TypeError, OverflowError, parser.ParserError):
        # এরর হলে সেইফ ডিফল্ট
        start_date = today.replace(day=1)
        end_date = today

    [(_emp, shift, days)] = att_rules.build_attendance([employee], start_date, end_date)
    return employee, user_company, start_date, end_date, days, att_rules.summarize(days, shift)


@login_required
def employee_attendance_detail(request, employee_id):
    employee, _company, start_date, end_date, days, s = _employee_report(request, employee_id)

    context = {
        'employee': employee,
        'attendance_summary': [(d['date'], d) for d in days],
        'start_date': start_date.strftime("%Y-%m-%d"),  # স্ট্রিং হিসেবে পাঠানো হচ্ছে যাতে ফর্মে ভ্যালু থাকে
        'end_date': end_date.strftime("%Y-%m-%d"),
        'total_work_duration': s['total_work'],

        'present_days': s['present'],
        'approved_leave_count': s['leave'],
        'absent_days': s['absent'],
        'public_holiday_count': s['holiday'],
        'weekly_holiday_count': s['weekly_off'],

        'total_leave_requests': LeaveRequest.objects.filter(
            employee=employee, status='Approved', start_date__lte=end_date, end_date__gte=start_date
        ).count(),
    }

    return render(request, 'employee_attendance_detail.html', context)


# ----------------------attendance details pdf-------------

@login_required
def employee_attendance_pdf(request, employee_id):
    emp, _company, start_date, end_date, days, s = _employee_report(request, employee_id)
    fmt = att_rules.format_timedelta
    context = {
        'employee': emp,
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'stats': {
            'present': s['present'], 'absent': s['absent'], 'leave': s['leave'],
            'off': s['holiday'] + s['weekly_off'],
            'total_work': fmt(s['total_work']), 'total_over': fmt(s['total_over']),
            'total_less': fmt(s['total_less']), 'total_late': fmt(s['total_late']),
        },
    }
    return render_pdf(request, 'details_pdf_template.html', context, f"Attendance_{emp.name}_{start_date}_to_{end_date}.pdf")


# ----------attendance_pdf_report-------------
# আগে এটা employee_attendance_pdf এর প্রায় হুবহু কপি ছিল; এখন দুটো URL একই রিপোর্ট দেয়
attendance_pdf_report = employee_attendance_pdf



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

def get_leave_summary_data(user, get_params, scope=None):
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
    dept_id = int_param(get_params, 'department')
    
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
    qs = LeaveRequest.objects.filter(company=user_company)
    if scope is not None:
        qs = scoping.leaves(qs, scope)
    qs = qs.select_related(
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
        'q': q, 'status': status, 'department': str(dept_id) if dept_id else '',
        'date_from': d_from, 'date_to': d_to,
        'departments': (scoping.departments(Department.objects.all(), scope) if scope else Department.objects.filter(company=user_company)).only('id', 'name').order_by('name'),
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
    leaves = scoping.leaves(LeaveRequest.objects.select_related('employee', 'employee__department'), scoping.get_scope(request))

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
    context = get_leave_summary_data(request.user, request.GET, scoping.get_scope(request))
    if 'error' in context:
        return HttpResponseForbidden(context['error'])
        
    return render(request, 'leave_summary.html', context)


@login_required
def leave_summary_pdf(request):
    """ PDF View: Uses same helper function """
    context = get_leave_summary_data(request.user, request.GET, scoping.get_scope(request))
    if 'error' in context:
        return HttpResponseForbidden(context['error'])
    context['department_name'] = _name_of(context['departments'], int_param(request.GET, 'department'))
    context['total_days'] = sum(i['total_days'] for i in context['summary'])
    context['total_requests'] = sum(i['requests'] for i in context['summary'])
    return render_pdf(request, 'leave_summary_pdf.html', context, f"Leave_Summary_{date.today()}.pdf")


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
    leave = get_object_or_404(scoping.leaves(LeaveRequest.objects.all(), scoping.get_scope(request).security), pk=pk)
    
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
    leave = get_object_or_404(scoping.leaves(LeaveRequest.objects.all(), scoping.get_scope(request).security), pk=pk)
    
    if request.method == 'POST':
        leave.delete()
        messages.warning(request, "Leave request deleted.")
        
    return redirect('attendance_app:leave_list')


# ----------------Holi day ---------------


@login_required
def holiday_list(request):
    user_company = getattr(request.user.profile, 'company', None)
    if user_company:
        holidays = scoping.holidays(Holiday.objects.select_related('branch'), scoping.get_scope(request))
    else:
        holidays = Holiday.objects.none()  # যদি company না থাকে, কিছু দেখাবেন না
    return render(request, 'holiday_list.html', {'holidays': holidays})

@login_required
def holiday_create(request):
    form = HolidayForm(request.POST or None, user=request.user)
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


def _editable_holidays(request):
    """ম্যানেজার শুধু নিজের ব্রাঞ্চের ছুটি বদলাতে/মুছতে পারে; সব-ব্রাঞ্চের ছুটি শুধু মালিক"""
    scope = scoping.get_scope(request)
    qs = Holiday.objects.filter(company=scope.company)
    return qs.filter(branch=scope.branch) if scope.locked else qs


@login_required
def holiday_edit(request, pk):
    user_company = getattr(request.user.profile, 'company', None)
    if not user_company:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')

    holiday = get_object_or_404(_editable_holidays(request), pk=pk)
    form = HolidayForm(request.POST or None, instance=holiday, user=request.user)
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

    holiday = get_object_or_404(_editable_holidays(request), pk=pk)
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

from django.core.cache import cache

# স্প্যাম ঠেকাতে: প্রতি ইউজার ও প্রতি IP থেকে ঘণ্টায় সর্বোচ্চ ৫টি টিকিট
SUPPORT_RATE_LIMIT = 5
SUPPORT_RATE_WINDOW = 60 * 60  # সেকেন্ড


def _support_rate_limited(request):
    """লিমিট পার হলে True; না হলে কাউন্টার বাড়িয়ে False"""
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    keys = [f"support_rl:user:{request.user.pk}", f"support_rl:ip:{ip}"]
    if any(cache.get(k, 0) >= SUPPORT_RATE_LIMIT for k in keys):
        return True
    for k in keys:
        # add() শুধু প্রথমবার সেট করে, তাই উইন্ডো প্রথম রিকোয়েস্ট থেকে গোনা হয়
        cache.add(k, 0, SUPPORT_RATE_WINDOW)
        try:
            cache.incr(k)
        except ValueError:
            cache.set(k, 1, SUPPORT_RATE_WINDOW)
    return False


@login_required
def support_page(request):
    if request.method == 'POST':
        is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
        if _support_rate_limited(request):
            msg = 'অনেকবার টিকিট পাঠানো হয়েছে। অনুগ্রহ করে এক ঘণ্টা পরে আবার চেষ্টা করুন।'
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=429)
            messages.error(request, msg)
            return render(request, 'support.html', status=429)

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
                recipient_list=[settings.SUPPORT_EMAIL],
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
    return render(request, '404.html', status=404)