# attendance_app/hr_views.py — HR Settings, Missing Out-Punch তালিকা, নোটিফিকেশন
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import scoping
from .hr import missing_punch
from .models import HRSettings, MissingPunch, Notification


class HRSettingsForm(forms.ModelForm):
    class Meta:
        model = HRSettings
        fields = ['enable_late_deduction', 'lates_equal_one_day_deduction', 'late_deduction_basis',
                  'enable_missing_punch_alert', 'enable_gps_attendance',
                  'alert_channel', 'enable_daily_absence_alert', 'daily_alert_time', 'enable_salary_sms',
                  'auto_create_unknown_ids']
        widgets = {
            'daily_alert_time': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'alert_channel': forms.RadioSelect,
            'late_deduction_basis': forms.RadioSelect,
            'lates_equal_one_day_deduction': forms.NumberInput(attrs={'min': 1, 'max': 31}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('daily_alert_time', 'alert_channel'):
            self.fields[name].required = False

    def clean_daily_alert_time(self):
        return self.cleaned_data.get('daily_alert_time') or self.instance.daily_alert_time or '11:00'

    def clean_alert_channel(self):
        return self.cleaned_data.get('alert_channel') or self.instance.alert_channel or 'sms'


def _can_manage_hr(request):
    """HR নিয়ম বদলাতে পারে: superuser, কোম্পানির মালিক বা কোম্পানি-লেভেল অ্যাডমিন (ব্রাঞ্চ ম্যানেজার নয়)"""
    scope = scoping.get_scope(request)
    return scope.company is not None and (request.user.is_superuser or scope.is_company_admin)


@login_required
def hr_settings(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')
    if not _can_manage_hr(request):
        return HttpResponseForbidden("শুধু কোম্পানির মালিক/অ্যাডমিন HR সেটিং বদলাতে পারেন।")
    obj = HRSettings.for_company(scope.company)
    form = HRSettingsForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        s = form.save(commit=False)
        s.updated_by = request.user
        s.save()
        messages.success(request, "HR সেটিং সেভ হয়েছে। বেতনের পেজ ও PDF এখন নতুন নিয়মে হিসাব করবে।")
        return redirect('attendance_app:hr_settings')
    from .messaging.backends import configured
    return render(request, 'hr/hr_settings.html', {'form': form, 'settings_obj': obj,
                                                   'sms_ready': configured('sms'), 'wa_ready': configured('whatsapp')})


@login_required
def missing_punch_list(request):
    scope = scoping.get_scope(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    status = request.GET.get('status', MissingPunch.STATUS_OPEN)
    qs = (MissingPunch.objects.filter(company=scope.company)
          .select_related('employee', 'employee__department', 'employee__department__branch', 'resolved_by'))
    if scope.branch:
        qs = qs.filter(employee__department__branch=scope.branch)
    if status in (MissingPunch.STATUS_OPEN, MissingPunch.STATUS_RESOLVED):
        qs = qs.filter(status=status)
    hr = HRSettings.for_company(scope.company)
    return render(request, 'hr/missing_punches.html', {
        'page_obj': Paginator(qs, 50).get_page(request.GET.get('page')),
        'status': status,
        'alert_enabled': hr.enable_missing_punch_alert,
        'open_count': MissingPunch.objects.filter(company=scope.company, status=MissingPunch.STATUS_OPEN).count(),
        'can_manage': _can_manage_hr(request),
    })


@login_required
@require_POST
def missing_punch_check_now(request):
    """এখনই চেক (আজকের দিন) — রাতের অটো চেকের মতোই"""
    scope = scoping.get_scope(request)
    if not _can_manage_hr(request):
        return HttpResponseForbidden("অনুমতি নেই।")
    res = missing_punch.check_company(scope.company)
    messages.success(request, f"চেক শেষ: {res['created']} জন নতুন Missing Out-Punch, {res['resolved']} টা আগের সমস্যা মিটেছে।")
    return redirect('attendance_app:missing_punch_list')


@login_required
@require_POST
def missing_punch_resolve(request, pk):
    scope = scoping.get_scope(request)
    qs = MissingPunch.objects.filter(company=scope.company)
    if scope.locked:
        qs = qs.filter(employee__department__branch=scope.branch)
    m = get_object_or_404(qs, pk=pk)
    m.status, m.resolved_at, m.resolved_by = MissingPunch.STATUS_RESOLVED, timezone.now(), request.user
    m.save(update_fields=['status', 'resolved_at', 'resolved_by'])
    messages.success(request, f"{m.employee.name} ({m.date:%d %b}) — resolved হিসেবে চিহ্নিত।")
    nxt = request.POST.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect('attendance_app:missing_punch_list')


def _unknown_qs(request):
    from .models import UnknownPunch
    scope = scoping.get_scope(request)
    qs = UnknownPunch.objects.filter(company=scope.company).select_related('device', 'department', 'department__branch')
    if scope.branch:
        qs = qs.filter(department__branch=scope.branch)
    return scope, qs


@login_required
def unknown_punch_list(request):
    from django.db.models import Count, Max, Min
    scope, qs = _unknown_qs(request)
    if scope.company is None:
        return redirect('attendance_app:dashboard')
    status = request.GET.get('status', 'open')
    if status in ('open', 'resolved', 'ignored'):
        qs = qs.filter(status=status)
    # একই ID/মেশিনের পাঞ্চ এক সারিতে
    groups = (qs.values('raw_id', 'reason', 'device__name', 'department_id', 'department__name', 'status')
              .annotate(n=Count('id'), first=Min('timestamp'), last=Max('timestamp')).order_by('-last'))
    from .models import Department
    return render(request, 'hr/unknown_punches.html', {
        'groups': Paginator(list(groups), 50).get_page(request.GET.get('page')),
        'status': status,
        'departments': scoping.departments(Department.objects.select_related('branch'), scope),
        'recent': qs.order_by('-timestamp')[:20],
    })


@login_required
@require_POST
def unknown_punch_resolve(request):
    """অচেনা ID → নতুন কর্মচারী (বা Ignore)। কর্মচারী বানালে আগের পাঞ্চগুলো হাজিরায় বসে যায়।"""
    from django.core.exceptions import ValidationError
    from .devices.ingest import Punch, ingest
    from .models import Department, PunchLog
    from .services import create_employee_with_limit
    scope, qs = _unknown_qs(request)
    raw_id = (request.POST.get('raw_id') or '').strip()
    action = request.POST.get('action')
    rows = qs.filter(raw_id=raw_id, status='open')
    if action == 'ignore':
        n = rows.update(status='ignored', resolved_by=request.user, resolved_at=timezone.now())
        messages.success(request, f"{n} টা অচেনা পাঞ্চ উপেক্ষা করা হলো।")
        return redirect('attendance_app:unknown_punch_list')

    dept = scoping.departments(Department.objects.all(), scope).filter(pk=request.POST.get('department')).first()
    name = (request.POST.get('name') or '').strip()
    if not (raw_id.isdigit() and dept and name):
        messages.error(request, "নাম, ডিপার্টমেন্ট দিন (শুধু সংখ্যার ID থেকে কর্মচারী বানানো যায়)।")
        return redirect('attendance_app:unknown_punch_list')
    try:
        create_employee_with_limit(company=scope.company, name=name, device_user_id=int(raw_id), department=dept,
                                   sub_user=request.user)
    except ValidationError as e:
        messages.error(request, "; ".join(e.messages))
        return redirect('attendance_app:unknown_punch_list')
    # আগের পাঞ্চ (PunchLog এ employee ছাড়া রাখা) আবার বসাও — দিনের In/Out তৈরি হয়
    key = PunchLog.make_scope_key(scope.company.id, dept.branch_id)
    old = [Punch(uid=r.device_user_id, timestamp=r.timestamp, method=r.method, verify_code=r.verify_code, state=r.punch_state)
           for r in PunchLog.objects.filter(scope_key=key, device_user_id=int(raw_id), employee__isnull=True)]
    res = ingest(dept, old, source='pull') if old else {'created': 0}
    n = rows.update(status='resolved', resolved_by=request.user, resolved_at=timezone.now())
    messages.success(request, f"{name} (ID {raw_id}) যোগ হয়েছে — {n} টা আগের পাঞ্চ থেকে {res.get('created', 0)} টা হাজিরা বসেছে।")
    return redirect('attendance_app:unknown_punch_list')


def _leave_id_from(link):
    from django.urls import Resolver404, resolve
    try:
        match = resolve(link.split('?')[0])
    except Resolver404:
        return None
    return match.kwargs.get('pk') if match.url_name == 'leave_update' else None


@login_required
def notification_poll(request):
    """হেডারের পপ-আপ: since id এর পরের না-পড়া নোটিফিকেশন"""
    from django.http import JsonResponse
    try:
        since = int(request.GET.get('since') or 0)
    except ValueError:
        since = 0
    qs = Notification.objects.filter(user=request.user, is_read=False)
    new = list(qs.filter(id__gt=since).order_by('id')[:5].values('id', 'title', 'message', 'kind', 'link'))
    for n in new:
        n['url'] = reverse('attendance_app:notification_read', args=[n['id']])
        # ছুটির আবেদন: পপআপ থেকেই Approve/Reject করার জন্য ছুটির id
        n['leave_id'] = _leave_id_from(n.pop('link') or '') if n['kind'] == 'leave' else None
    return JsonResponse({'unread': qs.count(), 'items': new,
                         'last_id': max([n['id'] for n in new] + [since])})


@login_required
def notification_list(request):
    qs = Notification.objects.filter(user=request.user)
    page = Paginator(qs, 30).get_page(request.GET.get('page'))
    return render(request, 'hr/notifications.html', {'page_obj': page})


@login_required
@require_POST
def notification_read(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    if not n.is_read:
        n.is_read = True
        n.save(update_fields=['is_read'])
    if n.link and url_has_allowed_host_and_scheme(n.link, allowed_hosts={request.get_host()}):
        return redirect(n.link)
    return redirect('attendance_app:notification_list')


@login_required
@require_POST
def notification_read_all(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    nxt = request.POST.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect('attendance_app:notification_list')
