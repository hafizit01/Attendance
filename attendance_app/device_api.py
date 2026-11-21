"""
মেশিন যেসব URL এ নিজে থেকে ডাটা পাঠায় — লগইন ছাড়া (মেশিন চেনা হয় SN বা token দিয়ে)।

    /iclock/cdata, /iclock/getrequest, /iclock/devicecmd   ZKTeco ADMS (cloud push)
    /api/devices/<token>/hikvision/                       Hikvision HTTP push
    /api/devices/<token>/push/                            যেকোনো মেশিন/সফটওয়্যার — JSON
"""
import json
import logging

from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .devices import adms, hikvision
from .devices.ingest import ingest
from .models import Device, DeviceCommand
from .utils.zk_import import import_pushed

logger = logging.getLogger(__name__)

MAX_COMMANDS_PER_REQUEST = 20


def _text(body, status=200):
    return HttpResponse(body, status=status, content_type='text/plain; charset=utf-8')


def _client_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or None


def _touch(device, request, **fields):
    device.last_seen_at = timezone.now()
    device.last_ip_seen = _client_ip(request)
    for k, v in fields.items():
        setattr(device, k, v)
    Device.objects.filter(pk=device.pk).update(last_seen_at=device.last_seen_at,
                                               last_ip_seen=device.last_ip_seen, **fields)


def _adms_device(request):
    sn = (request.GET.get('SN') or request.GET.get('sn') or '').strip()
    if not sn:
        return None
    return (Device.objects.select_related('company', 'department', 'department__branch')
            .filter(serial_number=sn, protocol=Device.PROTOCOL_ZK_ADMS, is_active=True).first())


# ---------------------------------------------------------
# ZKTeco ADMS
# ---------------------------------------------------------
@csrf_exempt
def iclock(request, action):
    action = action.lower()
    device = _adms_device(request)
    if device is None:
        # অচেনা মেশিনকে OK বলা যাবে না — বললে মেশিন ডাটা "পাঠানো হয়েছে" ধরে নিয়ে আর পাঠাবে না
        logger.info("ADMS: unknown/inactive device SN=%s from %s", request.GET.get('SN'), _client_ip(request))
        return _text("Device not registered", status=403)

    if action in ('cdata', 'registry', 'push'):
        if request.method == 'GET' or action != 'cdata':
            _touch(device, request)
            return _text(adms.options_response(device))
        return _adms_upload(request, device)
    if action == 'getrequest':
        return _adms_getrequest(request, device)
    if action == 'devicecmd':
        return _adms_devicecmd(request, device)
    # ping / rtdata / querydata ইত্যাদি — শুধু উপস্থিতি
    _touch(device, request)
    return _text("OK")


def _adms_upload(request, device):
    table = (request.GET.get('table') or '').upper()
    stamp = (request.GET.get('Stamp') or request.GET.get('stamp') or '').strip()[:32]

    if table == 'ATTLOG':
        punches, bad = adms.parse_attlog(request.body)
        try:
            with transaction.atomic():
                res = ingest(device.department, punches, device=device, source='adms', create_unknown=None)
        except Exception:
            logger.exception("ADMS ATTLOG import failed for device %s", device.pk)
            # OK না দিলে মেশিন পরে আবার পাঠাবে — ডাটা হারায় না
            return _text("ERROR", status=500)
        fields = {'last_error': ''}
        if stamp:
            fields['adms_att_stamp'] = stamp
        _touch(device, request, **fields)
        if bad:
            logger.info("ADMS device %s: %s unreadable ATTLOG line(s)", device.pk, bad)
        logger.info("ADMS device %s: %s", device.pk, res)
        return _text(f"OK: {len(punches) + bad}")

    if table in ('OPERLOG', 'USERINFO', 'USER'):
        users, total = adms.parse_users(request.body)
        if users:
            try:
                with transaction.atomic():
                    ingest(device.department, [], users=users, device=device, source='adms')
            except Exception:
                logger.exception("ADMS user import failed for device %s", device.pk)
                return _text("ERROR", status=500)
        fields = {'adms_op_stamp': stamp} if stamp else {}
        _touch(device, request, **fields)
        return _text(f"OK: {total}")

    # ATTPHOTO, BIODATA, FINGERTMP, options ইত্যাদি — দরকার নেই, তবে গ্রহণ করি যাতে মেশিন আটকে না থাকে
    _touch(device, request)
    return _text(f"OK: {len(adms._lines(request.body))}")


def _adms_getrequest(request, device):
    fields = adms.parse_info(request.GET.get('INFO', ''))
    _touch(device, request, **fields)
    with transaction.atomic():
        cmds = list(DeviceCommand.objects.select_for_update()
                    .filter(device=device, status='pending').order_by('id')[:MAX_COMMANDS_PER_REQUEST])
        if not cmds:
            return _text("OK")
        DeviceCommand.objects.filter(pk__in=[c.pk for c in cmds]).update(status='sent', sent_at=timezone.now())
    return _text("\n".join(f"C:{c.pk}:{c.command}" for c in cmds) + "\n")


def _adms_devicecmd(request, device):
    _touch(device, request)
    now = timezone.now()
    for cid, ret in adms.parse_command_results(request.body):
        code = (ret or '').strip()
        ok = code in ('', '0') or (code.lstrip('-').isdigit() and int(code) >= 0)
        DeviceCommand.objects.filter(pk=cid, device=device).update(
            status='done' if ok else 'failed', return_code=code[:20], done_at=now)
    return _text("OK")


# ---------------------------------------------------------
# Token দিয়ে চেনা মেশিন (Hikvision push / JSON API)
# ---------------------------------------------------------
def _token_device(token, protocols):
    return (Device.objects.select_related('company', 'department', 'department__branch')
            .filter(push_token=token, protocol__in=protocols, is_active=True).first())


@csrf_exempt
def hikvision_push(request, token):
    device = _token_device(token, [Device.PROTOCOL_HIK_PUSH, Device.PROTOCOL_HIK_ISAPI])
    if device is None:
        return JsonResponse({'status': 'error', 'message': 'Unknown device token'}, status=403)
    if request.method != 'POST':
        _touch(device, request)
        return JsonResponse({'status': 'ok'})

    data = hikvision.parse_push_event(request)
    punch = hikvision.punch_from_push(data) if data else None
    if punch is not None:
        try:
            with transaction.atomic():
                ingest(device.department, [punch], device=device, source='hik', create_unknown=None)
        except Exception:
            logger.exception("Hikvision push import failed for device %s", device.pk)
            return JsonResponse({'status': 'error'}, status=500)
    stranger = hikvision.stranger_from_push(data) if data and punch is None else None
    if stranger:
        from .hr import unknown as unknown_punches
        unknown_punches.record(device.company, [(stranger[0], stranger[1], 'face')], device=device,
                               department=device.department, reason='stranger')
    _touch(device, request, last_error='')
    return JsonResponse({'status': 'ok', 'accepted': 1 if punch else 0, 'stranger': bool(stranger)})


@csrf_exempt
def api_push(request, token):
    """
    যেকোনো মেশিন / থার্ড-পার্টি সফটওয়্যারের জন্য:
        POST {"users": [{"uid": 1, "name": "Rahim"}],
              "punches": [{"uid": 1, "time": "2026-11-18 09:01:02", "method": "face"}]}
    """
    device = _token_device(token, [Device.PROTOCOL_API])
    if device is None:
        return JsonResponse({'status': 'error', 'message': 'Unknown device token'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST JSON required'}, status=405)
    try:
        data = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    if isinstance(data, list):
        data = {'punches': data}
    users, punches = data.get('users') or [], data.get('punches') or []
    if not isinstance(users, list) or not isinstance(punches, list):
        return JsonResponse({'status': 'error', 'message': 'users and punches must be lists'}, status=400)
    with transaction.atomic():
        res = import_pushed(device.department, users, punches, device=device, source='api')
    _touch(device, request, last_error='')
    return JsonResponse(res)
