# payroll/message_views.py — "বেতন দেওয়া হয়েছে" SMS/WhatsApp (পে-স্লিপের নিরাপদ লিংকসহ)
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from attendance_app import scoping
from attendance_app.models import HRSettings
from attendance_app.tasks import dispatch
from attendance_app.utils.params import month_param

from .views import can_change_payroll_settings


@login_required
@require_POST
def send_salary_sms(request):
    scope = scoping.get_scope(request)
    company = scope.company
    if company is None or scope.locked or not can_change_payroll_settings(request.user, company):
        return HttpResponseForbidden("শুধু কোম্পানির মালিক/অ্যাডমিন বেতনের SMS পাঠাতে পারেন।")
    month = month_param(request.POST, 'month')
    back = reverse('payroll:salary_summary_list') + (f"?month={month}" if month else '')
    if not month:
        messages.error(request, "মাস বাছাই করুন।")
        return redirect(back)
    if not HRSettings.for_company(company).enable_salary_sms:
        messages.error(request, "HR Rules এ 'বেতন দেওয়ার SMS' চালু নেই।")
        return redirect(back)
    res = dispatch('send_salary_disbursed', company.id, month, request.build_absolute_uri('/'))
    if res is None:
        messages.success(request, "বেতনের SMS পাঠানো শুরু হয়েছে (ব্যাকগ্রাউন্ডে) — Message log এ ফলাফল দেখুন।")
    else:
        parts = [f"{res.get('sent', 0)} sent"]
        if res.get('logged'):
            parts.append(f"{res['logged']} only logged (SMS gateway সেট করা নেই)")
        if res.get('failed'):
            parts.append(f"{res['failed']} failed")
        if res.get('no_phone'):
            parts.append(f"{res['no_phone']} জনের মোবাইল নম্বর নেই (Employee Profiles)")
        (messages.success if not res.get('failed') else messages.warning)(request, "Salary SMS: " + ", ".join(parts))
    return redirect(back)
