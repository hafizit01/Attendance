# attendance_app/portal/views.py — কর্মচারীর সেলফ-সার্ভিস পোর্টাল (/me/)
import calendar
from datetime import date, datetime, timedelta
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from attendance_app.hr import leave_balance
from attendance_app.models import Attendance, HRSettings, LeaveRequest
from attendance_app.utils import attendance_helpers as att_rules
from attendance_app.utils.attendance_helpers import day_range
from attendance_app.utils.params import month_param
from attendance_app.utils.pdf import render_pdf
from subscription_app.utils import get_company_subscription

from . import services
from .access import is_admin_user


def portal_view(view):
    """লগইন + কর্মচারীর সাথে যুক্ত লগইন + কোম্পানির সাবস্ক্রিপশন চালু"""
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        emp = getattr(request.user, 'employee', None)
        if emp is None:
            if is_admin_user(request.user):
                messages.info(request, "আপনার লগইন কোনো কর্মচারীর সাথে যুক্ত নয় — এটা কর্মচারীদের পোর্টাল।")
                return redirect('attendance_app:dashboard')
            return render(request, 'portal/blocked.html', {'reason': 'no_employee'}, status=403)
        if not emp.is_active:
            return render(request, 'portal/blocked.html', {'reason': 'inactive', 'employee': emp}, status=403)
        if not get_company_subscription(emp.company):
            return render(request, 'portal/blocked.html', {'reason': 'subscription', 'employee': emp}, status=403)
        request.employee = emp
        return view(request, *args, **kwargs)
    return wrapped


def _hr(emp):
    return HRSettings.for_company(emp.company)


def _common(request, active):
    emp = request.employee
    return {'employee': emp, 'active_tab': active, 'gps_enabled': _hr(emp).enable_gps_attendance,
            'is_admin_too': is_admin_user(request.user)}


# ---------------------------------------------------------
# হোম + হাজিরার ক্যালেন্ডার
# ---------------------------------------------------------
@portal_view
def home(request):
    emp = request.employee
    today = timezone.localdate()
    month = month_param(request.GET, 'month', today.strftime('%Y-%m'))
    y, m = map(int, month.split('-'))
    first = date(y, m, 1)
    last = date(y, m, calendar.monthrange(y, m)[1])
    end = min(last, today) if first <= today else last
    days = []
    shift = None
    if first <= today:
        [(_e, shift, days)] = att_rules.build_attendance([emp], first, end)
    summary = att_rules.summarize(days, shift) if days else None
    by_date = {d['date']: d for d in days}

    # ক্যালেন্ডার গ্রিড (শনিবার থেকে সপ্তাহ শুরু)
    cal = calendar.Calendar(firstweekday=5)
    weeks = [[{'date': d, 'in_month': d.month == m, 'day': by_date.get(d), 'future': d > today, 'today': d == today}
              for d in week] for week in cal.monthdatescalendar(y, m)]

    today_rec = by_date.get(today) if first <= today <= last else None
    if today_rec is None and first.month != today.month:
        [(_e, _s, td)] = att_rules.build_attendance([emp], today, today)
        today_rec = td[0]
    prev_m = (first.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    next_m = (last + timedelta(days=1)).strftime('%Y-%m')
    recent_remote = Attendance.objects.filter(employee=emp, is_remote_punch=True).order_by('-timestamp')[:3]
    return render(request, 'portal/home.html', {
        **_common(request, 'home'),
        'weeks': weeks, 'month_label': first.strftime('%B %Y'), 'summary': summary,
        'prev_month': prev_m, 'next_month': next_m if first <= today.replace(day=1) else None,
        'today_rec': today_rec, 'today': today,
        'balances': leave_balance.balance(emp, today.year),
        'recent_remote': recent_remote,
        'weekdays': ['Sat', 'Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
    })


# ---------------------------------------------------------
# ছুটি
# ---------------------------------------------------------
class PortalLeaveForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ['leave_type', 'start_date', 'end_date', 'reason']
        widgets = {'start_date': forms.DateInput(attrs={'type': 'date'}),
                   'end_date': forms.DateInput(attrs={'type': 'date'}),
                   'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'ছুটির কারণ'})}

    def __init__(self, *args, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.employee = employee
        self.fields['reason'].required = True

    def clean(self):
        c = super().clean()
        s, e, lt = c.get('start_date'), c.get('end_date'), c.get('leave_type')
        if s and e:
            if e < s:
                self.add_error('end_date', "শেষ তারিখ শুরুর আগে হতে পারে না।")
                return c
            if s < timezone.localdate() - timedelta(days=30):
                self.add_error('start_date', "৩০ দিনের বেশি পুরনো ছুটির আবেদন অফিসে জানান।")
            overlap = LeaveRequest.objects.filter(employee=self.employee, status__in=('Pending', 'Approved'),
                                                  start_date__lte=e, end_date__gte=s).exists()
            if overlap:
                raise ValidationError("এই তারিখগুলোতে আপনার আরেকটা ছুটির আবেদন আছে।")
            err = leave_balance.validate_request(self.employee, lt, s, e)
            if err:
                raise ValidationError(err)
        return c


@portal_view
def leave(request):
    emp = request.employee
    form = PortalLeaveForm(request.POST or None, employee=emp)
    if request.method == 'POST' and form.is_valid():
        lv = form.save(commit=False)
        lv.employee, lv.company, lv.status = emp, emp.company, 'Pending'
        lv.save()
        _notify_admins_leave(lv)
        messages.success(request, "ছুটির আবেদন জমা হয়েছে — অফিস approve করলে ব্যালান্স থেকে কাটা হবে।")
        return redirect('portal:leave')
    year = timezone.localdate().year
    return render(request, 'portal/leave.html', {
        **_common(request, 'leave'), 'form': form,
        'balances': leave_balance.balance(emp, year), 'year': year,
        'leaves': LeaveRequest.objects.filter(employee=emp).order_by('-start_date', '-id')[:30],
    })


def _notify_admins_leave(lv):
    from django.urls import reverse
    from attendance_app.hr.missing_punch import _recipients
    from attendance_app.models import Notification
    branch_id = lv.employee.department.branch_id if lv.employee.department_id else None
    days = (lv.end_date - lv.start_date).days + 1
    Notification.objects.bulk_create([
        Notification(company=lv.company, user=u, kind='info',
                     title=f"ছুটির আবেদন: {lv.employee.name}",
                     message=f"{lv.get_leave_type_display()} · {lv.start_date:%d %b} – {lv.end_date:%d %b} ({days} দিন)। {lv.reason or ''}"[:500],
                     link=reverse('attendance_app:leave_update', args=[lv.pk]))
        for u, b in _recipients(lv.company) if b is None or b == branch_id
    ])


@portal_view
@require_POST
def leave_cancel(request, pk):
    lv = get_object_or_404(LeaveRequest, pk=pk, employee=request.employee, status='Pending')
    lv.delete()
    messages.success(request, "ছুটির আবেদন বাতিল হয়েছে।")
    return redirect('portal:leave')


# ---------------------------------------------------------
# পে-স্লিপ
# ---------------------------------------------------------
@portal_view
def payslips(request):
    emp = request.employee
    months = services.payslip_months(emp)
    return render(request, 'portal/payslips.html', {**_common(request, 'payslips'), 'months': months,
                                                    'has_salary': hasattr(emp, 'employeesalary')})


@portal_view
def payslip_pdf(request, month):
    return _payslip_response(request, request.employee, month)


def public_payslip(request, token):
    """SMS এর নিরাপদ লিংক: লগইন ছাড়া, শুধু ঐ কর্মচারীর ঐ মাস, ৪৫ দিন মেয়াদ"""
    from django.core import signing
    from attendance_app.messaging.alerts import read_payslip_token
    from attendance_app.models import Employee
    try:
        emp_id, month = read_payslip_token(token)
    except (signing.BadSignature, ValueError):
        return render(request, 'portal/blocked.html', {'reason': 'link'}, status=404)
    emp = Employee.objects.select_related('company', 'department', 'department__branch').filter(pk=emp_id).first()
    if emp is None or not get_company_subscription(emp.company):
        return render(request, 'portal/blocked.html', {'reason': 'link'}, status=404)
    return _payslip_response(request, emp, month)


def _payslip_response(request, emp, month):
    try:
        m = datetime.strptime(month, '%Y-%m').date()
    except ValueError:
        raise Http404
    if m not in services.payslip_months(emp, limit=120):
        raise Http404("এই মাসের পে-স্লিপ নেই।")
    slip = services.payslip(emp, month)
    if slip is None:
        raise Http404("বেতন সেট করা নেই।")
    from payroll.payslip import payslip_lines
    ctx = {'slip': slip, 'lines': payslip_lines(slip), 'employee': emp, 'month_label': m.strftime('%B %Y'),
           'company_name': emp.company.name,
           'branch_name': emp.department.branch.name if emp.department_id and emp.department.branch_id else '',
           'generated_at': timezone.localtime(), 'generated_by': emp.name}
    return render_pdf(request, 'portal/payslip_pdf.html', ctx, f"Payslip_{emp.name}_{month}.pdf")


# ---------------------------------------------------------
# GPS + সেলফি পাঞ্চ
# ---------------------------------------------------------
@portal_view
def punch_page(request):
    if not _hr(request.employee).enable_gps_attendance:
        messages.error(request, "আপনার অফিসে মোবাইল থেকে হাজিরা চালু নেই।")
        return redirect('portal:home')
    today = timezone.localdate()
    todays = Attendance.objects.filter(employee=request.employee, **day_range(today)).order_by('timestamp')
    return render(request, 'portal/punch.html', {**_common(request, 'punch'), 'todays': todays,
                                                 'next_status': 'Out' if todays.filter(status='In').exists() else 'In'})


@portal_view
@require_POST
def punch_submit(request):
    emp = request.employee
    if not _hr(emp).enable_gps_attendance:
        return JsonResponse({'status': 'error', 'message': 'মোবাইল থেকে হাজিরা চালু নেই।'}, status=403)
    try:
        att = services.remote_punch(
            emp, latitude=request.POST.get('latitude'), longitude=request.POST.get('longitude'),
            accuracy=request.POST.get('accuracy'), selfie_data=request.POST.get('selfie'),
            selfie_upload=request.FILES.get('selfie_file'), note=request.POST.get('note', ''))
    except ValidationError as e:
        return JsonResponse({'status': 'error', 'message': ' '.join(e.messages)}, status=400)
    local = timezone.localtime(att.timestamp)
    return JsonResponse({'status': 'success', 'punch': att.status, 'time': local.strftime('%I:%M %p'),
                         'message': f"Punch {att.status} সফল — {local:%I:%M %p}"})
