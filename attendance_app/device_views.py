# attendance_app/device_views.py — হাজিরা মেশিন: তালিকা, যোগ/এডিট, সেটআপ গাইড, কমান্ড, ফাইল ইমপোর্ট, লাইভ পাঞ্চ
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import scoping
from .device_forms import DeviceForm, PunchImportForm
from .devices import fileimport, service
from .devices.ingest import ingest, summary
from .models import Device, PunchLog
from .utils.params import int_param


def _devices(request):
    scope = scoping.get_scope(request)
    qs = Device.objects.select_related('department', 'department__branch').filter(company=scope.company)
    if scope.branch:
        qs = qs.filter(department__branch=scope.branch)
    return scope, qs


def _can_manage(request):
    """মেশিনের কানেকশন সেটিং (IP, পাসওয়ার্ড, SN) বদলানো — আগের মতোই শুধু superuser"""
    return request.user.is_superuser


def _is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


def _server_urls(request, device):
    host = request.get_host()
    scheme = 'https' if request.is_secure() else 'http'
    hostname, _, port = host.partition(':')
    return {
        'host': hostname,
        'port': port or ('443' if request.is_secure() else '80'),
        'https': request.is_secure(),
        'base': f"{scheme}://{host}",
        'hik_push': f"{scheme}://{host}{reverse('device_hikvision_push', args=[device.push_token])}",
        'api_push': f"{scheme}://{host}{reverse('device_api_push', args=[device.push_token])}",
        'hik_path': reverse('device_hikvision_push', args=[device.push_token]),
    }


@login_required
def device_list(request):
    scope, qs = _devices(request)
    if scope.company is None:
        messages.error(request, "আপনার কোম্পানি সেট করা নেই।")
        return redirect('attendance_app:dashboard')
    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    devices = list(qs.annotate(punches_today=Count('punches', filter=Q(punches__timestamp__gte=today_start))))
    online = sum(1 for d in devices if d.is_online)
    return render(request, 'devices/device_list.html', {
        'devices': devices,
        'online_count': online,
        'offline_count': len(devices) - online,
        'punches_today': sum(d.punches_today for d in devices),
        'can_manage': _can_manage(request),
    })


@login_required
def device_form(request, pk=None):
    if not _can_manage(request):
        return HttpResponseForbidden("শুধু admin মেশিন যোগ/এডিট করতে পারেন।")
    scope, qs = _devices(request)
    device = get_object_or_404(qs, pk=pk) if pk else None
    form = DeviceForm(request.POST or None, instance=device, scope=scope)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.company = scope.company
        obj.save()
        messages.success(request, f"Device “{obj.name}” {'updated' if pk else 'added'}.")
        return redirect('attendance_app:device_detail', pk=obj.pk)
    return render(request, 'devices/device_form.html', {
        'form': form, 'device': device, 'title': 'Edit device' if pk else 'Add device',
    })


@login_required
@require_POST
def device_delete(request, pk):
    if not _can_manage(request):
        return HttpResponseForbidden("শুধু admin মেশিন মুছতে পারেন।")
    _, qs = _devices(request)
    device = get_object_or_404(qs, pk=pk)
    name = device.name
    device.delete()  # পাঞ্চ লগ ও হাজিরা থেকে যায় (device=NULL)
    messages.success(request, f"Device “{name}” removed. আগের হাজিরা ঠিক আছে।")
    return redirect('attendance_app:device_list')


@login_required
def device_detail(request, pk):
    _, qs = _devices(request)
    device = get_object_or_404(qs, pk=pk)
    punches = (PunchLog.objects.filter(device=device).select_related('employee')
               .order_by('-timestamp')[:25])
    methods = (PunchLog.objects.filter(device=device).values('method')
               .annotate(n=Count('id')).order_by('-n'))
    return render(request, 'devices/device_detail.html', {
        'device': device,
        'actions': [(a, service.ACTIONS[a]) for a in service.available_actions(device)],
        'punches': punches,
        'methods': methods,
        'commands': device.commands.all()[:15],
        'pending_commands': device.commands.filter(status__in=['pending', 'sent']).count(),
        'urls': _server_urls(request, device),
        'import_form': PunchImportForm(),
        'can_manage': _can_manage(request),
        'show_secrets': _can_manage(request),
    })


@login_required
@require_POST
def device_action(request, pk):
    _, qs = _devices(request)
    device = get_object_or_404(qs, pk=pk)
    action = request.POST.get('action', '')
    if action in ('restart',) and not scoping.get_scope(request).is_company_admin and not request.user.is_superuser:
        msg = "শুধু কোম্পানি অ্যাডমিন মেশিন রিস্টার্ট করতে পারেন।"
        ok = False
    else:
        try:
            msg, ok = service.run_action(device, action, user=request.user), True
        except service.DeviceActionError as e:
            msg, ok = str(e), False
    if _is_ajax(request):
        return JsonResponse({'status': 'success' if ok else 'error', 'message': msg})
    (messages.success if ok else messages.error)(request, f"{device.name}: {msg}")
    return redirect(request.POST.get('next') or reverse('attendance_app:device_detail', args=[device.pk]))


@login_required
@require_POST
def device_import(request, pk):
    _, qs = _devices(request)
    device = get_object_or_404(qs, pk=pk)
    form = PunchImportForm(request.POST, request.FILES)
    if not form.is_valid():
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
        return redirect('attendance_app:device_detail', pk=device.pk)
    punches, bad = fileimport.parse(form.cleaned_data['file'].read())
    if not punches:
        messages.error(request, "ফাইলে কোনো পাঞ্চ পাওয়া যায়নি। attlog.dat বা ID,তারিখ-সময় কলামওয়ালা CSV দিন।")
        return redirect('attendance_app:device_detail', pk=device.pk)
    with transaction.atomic():
        res = ingest(device.department, punches, device=device, source='file',
                     create_unknown=form.cleaned_data['create_employees'])
    msg = f"File import: {len(punches)} punches read · " + summary(res)
    if bad:
        msg += f" · {bad} line(s) skipped"
    messages.success(request, msg)
    return redirect('attendance_app:device_detail', pk=device.pk)


def dashboard_widgets(scope):
    """ড্যাশবোর্ডের জন্য: মেশিন অনলাইন/মোট ও সর্বশেষ পাঞ্চ (মেশিন না থাকলে খালি)"""
    devices = Device.objects.filter(company=scope.company, is_active=True)
    punches = PunchLog.objects.filter(company=scope.company)
    if scope.branch:
        devices = devices.filter(department__branch=scope.branch)
        punches = punches.filter(branch=scope.branch)
    devices = list(devices)
    return {
        'device_total': len(devices),
        'device_online': sum(1 for d in devices if d.is_online),
        'recent_punches': list(punches.select_related('employee', 'device').order_by('-timestamp', '-id')[:6]),
    }


@login_required
def device_status_json(request):
    """ড্যাশবোর্ড/তালিকার জন্য — কোন মেশিন অনলাইন"""
    _, qs = _devices(request)
    return JsonResponse({'devices': [{
        'id': d.id, 'name': d.name, 'online': d.is_online,
        'last_seen': timezone.localtime(d.last_seen_at).strftime('%d %b %I:%M %p') if d.last_seen_at else None,
    } for d in qs]})


@login_required
def punch_feed(request):
    """লাইভ পাঞ্চ: মেশিনে কেউ ফেস/ফিঙ্গার দিলেই এখানে দেখা যায় (রিয়েল-টাইম মেশিনে)"""
    scope = scoping.get_scope(request)
    qs = (PunchLog.objects.filter(company=scope.company)
          .select_related('employee', 'device', 'branch'))
    if scope.branch:
        qs = qs.filter(branch=scope.branch)
    device_id = int_param(request.GET, 'device')
    method = request.GET.get('method') or ''
    if device_id:
        qs = qs.filter(device_id=device_id)
    if method in dict(PunchLog.METHOD_CHOICES):
        qs = qs.filter(method=method)
    if request.GET.get('unknown'):
        qs = qs.filter(employee__isnull=True)
    page = Paginator(qs.order_by('-timestamp', '-id'), 50).get_page(request.GET.get('page'))

    if request.GET.get('partial'):
        return render(request, 'devices/_punch_rows.html', {'punches': page.object_list})

    today_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    today = PunchLog.objects.filter(company=scope.company, timestamp__gte=today_start)
    if scope.branch:
        today = today.filter(branch=scope.branch)
    _, devices = _devices(request)
    return render(request, 'devices/punch_feed.html', {
        'page_obj': page,
        'punches': page.object_list,
        'devices': devices,
        'methods': PunchLog.METHOD_CHOICES,
        'selected_device': device_id,
        'selected_method': method,
        'unknown_only': bool(request.GET.get('unknown')),
        'today_total': today.count(),
        'today_people': today.exclude(employee__isnull=True).values('employee').distinct().count(),
        'today_by_method': list(today.values('method').annotate(n=Count('id')).order_by('-n')),
        'live': not request.GET.get('page') or request.GET.get('page') == '1',
    })
