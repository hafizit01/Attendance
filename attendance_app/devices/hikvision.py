"""
Hikvision ফেস / ফিঙ্গার / কার্ড টার্মিনাল (DS-K1T341, DS-K1T671, DS-K1T804 ইত্যাদি) — ISAPI (HTTP + Digest auth)।

দুইভাবে চলে:
  • LAN pull  — সার্ভার মেশিনের IP তে গিয়ে ইভেন্ট পড়ে (এই ফাইল)
  • HTTP push — মেশিন নিজে সার্ভারে ইভেন্ট পাঠায় (device_api.hikvision_push + parse_push_event)
"""
import json
import uuid
from datetime import datetime, timedelta

from django.utils import timezone

from attendance_app.utils.zk_import import _to_int
from .ingest import Punch, ingest, method_from_text

PAGE = 30
# ইভেন্টের minor কোড → পদ্ধতি (major 5 = access control event)। 76 = face authentication FAILED (অচেনা মুখ)
MINOR_METHOD = {1: 'card', 38: 'finger', 75: 'face'}
MINOR_FACE_FAILED = 76
HIK_TIME = "%Y-%m-%dT%H:%M:%S"


class HikError(Exception):
    pass


def _session(device):
    import requests
    from requests.auth import HTTPDigestAuth
    s = requests.Session()
    s.auth = HTTPDigestAuth(device.username or 'admin', device.password or '')
    s.headers['Content-Type'] = 'application/json'
    return s


def _base(device):
    return f"http://{device.ip}:{device.port or 80}"


def _post(s, url, body, timeout=15):
    r = s.post(url, data=json.dumps(body), timeout=timeout)
    if r.status_code == 401:
        raise HikError("Login failed — username/password ভুল")
    if r.status_code >= 400:
        raise HikError(f"Device error {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError:
        raise HikError("Device did not return JSON (ISAPI access control সাপোর্ট করে কিনা দেখুন)")


def _local_iso(dt):
    dt = timezone.localtime(dt) if timezone.is_aware(dt) else dt
    offset = timezone.localtime().strftime('%z')
    return dt.strftime(HIK_TIME) + f"{offset[:3]}:{offset[3:]}"


def parse_time(text):
    text = (text or '').strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    return timezone.localtime(dt) if dt.tzinfo else dt


def event_to_punch(ev):
    """একটা AcsEvent / AccessControllerEvent → Punch (কর্মচারী নেই এমন ইভেন্ট হলে None)"""
    uid = _to_int(ev.get('employeeNoString') or ev.get('employeeNo'))
    ts = parse_time(ev.get('time') or ev.get('dateTime'))
    if uid is None or ts is None:
        return None
    minor = _to_int(ev.get('minor') or ev.get('subEventType'))
    method = MINOR_METHOD.get(minor) or method_from_text(ev.get('currentVerifyMode'))
    state = {'checkIn': 0, 'checkOut': 1, 'breakOut': 2, 'breakIn': 3,
             'overtimeIn': 4, 'overtimeOut': 5}.get(ev.get('attendanceStatus'))
    return Punch(uid=uid, timestamp=ts, method=method, verify_code=minor, state=state)


def fetch_events(device, start, end):
    s = _session(device)
    url = f"{_base(device)}/ISAPI/AccessControl/AcsEvent?format=json"
    search_id, pos, out = uuid.uuid4().hex, 0, []
    while True:
        data = _post(s, url, {"AcsEventCond": {
            "searchID": search_id, "searchResultPosition": pos, "maxResults": PAGE,
            "major": 5, "minor": 0, "startTime": _local_iso(start), "endTime": _local_iso(end),
        }}).get('AcsEvent', {})
        for ev in data.get('InfoList') or []:
            p = event_to_punch(ev)
            if p:
                out.append(p)
        n = _to_int(data.get('numOfMatches')) or 0
        pos += n
        if data.get('responseStatusStrg') != 'MORE' or n == 0:
            return out


def fetch_users(device):
    from types import SimpleNamespace
    s = _session(device)
    url = f"{_base(device)}/ISAPI/AccessControl/UserInfo/Search?format=json"
    search_id, pos, out = uuid.uuid4().hex, 0, []
    while True:
        data = _post(s, url, {"UserInfoSearchCond": {
            "searchID": search_id, "searchResultPosition": pos, "maxResults": PAGE,
        }}).get('UserInfoSearch', {})
        for u in data.get('UserInfo') or []:
            out.append(SimpleNamespace(user_id=u.get('employeeNo'), name=u.get('name') or ''))
        n = _to_int(data.get('numOfMatches')) or 0
        pos += n
        if data.get('responseStatusStrg') != 'MORE' or n == 0:
            return out


def device_info(device):
    import re
    s = _session(device)
    r = s.get(f"{_base(device)}/ISAPI/System/deviceInfo", timeout=8)
    if r.status_code == 401:
        raise HikError("Login failed — username/password ভুল")
    r.raise_for_status()
    tag = lambda t: (re.search(rf"<{t}>(.*?)</{t}>", r.text) or [None, ''])[1].strip()
    return {'model_name': tag('model')[:100], 'firmware': tag('firmwareVersion')[:100],
            'serial_number': tag('serialNumber')[:64]}


def sync(device, full=False):
    now = timezone.now()
    if full or not device.last_synced_at:
        start = now - timedelta(days=90 if full else 30)
    else:
        start = device.last_synced_at - timedelta(days=1)
    users = fetch_users(device)
    punches = fetch_events(device, start, now)
    res = ingest(device.department, punches, users=users, device=device, source='hik', create_unknown=False)
    info = {}
    try:
        info = device_info(device)
    except Exception:
        pass
    for k, v in info.items():
        if v and not (k == 'serial_number' and device.serial_number):
            setattr(device, k, v)
    from attendance_app.models import Device
    if device.serial_number and Device.objects.filter(serial_number=device.serial_number).exclude(pk=device.pk).exists():
        device.serial_number = None
    device.user_count = len(users)
    device.last_seen_at = timezone.now()
    device.last_error = ''
    device.save()
    return res


def test(device):
    return device_info(device)


def set_time(device):
    s = _session(device)
    now = timezone.localtime()
    xml = ("<?xml version='1.0' encoding='UTF-8'?><Time><timeMode>manual</timeMode>"
           f"<localTime>{now.strftime(HIK_TIME)}</localTime></Time>")
    r = s.put(f"{_base(device)}/ISAPI/System/time", data=xml.encode(),
              headers={'Content-Type': 'application/xml'}, timeout=10)
    if r.status_code >= 400:
        raise HikError(f"Device error {r.status_code}")


def restart(device):
    s = _session(device)
    r = s.put(f"{_base(device)}/ISAPI/System/reboot", timeout=10)
    if r.status_code >= 400:
        raise HikError(f"Device error {r.status_code}")


def push_users(device, employees):
    s = _session(device)
    added = updated = 0
    for emp in employees:
        info = {"UserInfo": {
            "employeeNo": str(emp.device_user_id), "name": (emp.name or '')[:32], "userType": "normal",
            "Valid": {"enable": True, "beginTime": "2020-01-01T00:00:00", "endTime": "2037-12-31T23:59:59"},
            "doorRight": "1", "RightPlan": [{"doorNo": 1, "planTemplateNo": "1"}],
        }}
        r = s.post(f"{_base(device)}/ISAPI/AccessControl/UserInfo/Record?format=json",
                   data=json.dumps(info), timeout=10)
        if r.status_code < 400:
            added += 1
            continue
        # মেশিনে আগে থেকেই আছে: আর Modify করি না — করলে ইউজারের ধরন, মেয়াদ ও দরজার অনুমতি
        # সফটওয়্যারের ডিফল্টে বদলে যেত। ফেস/কার্ড/নাম যেমন আছে তেমনই থাকে।
        if _already_exists(r):
            updated += 1   # "আগে থেকেই আছে" (কিছু বদলানো হয়নি)
            continue
        raise HikError(f"{emp.name}: device error {r.status_code} {r.text[:120]}")
    return added, updated


def _already_exists(resp):
    text = (resp.text or '').lower()
    return 'exist' in text or 'duplicate' in text or 'employeenoalreadyexist' in text


def parse_push_event(request):
    """
    মেশিনের HTTP push (Event alarm → HTTP listening): JSON বডি, অথবা multipart এর
    'event_log' / 'AccessControllerEvent' অংশে JSON (সাথে ছবি থাকতে পারে)।
    রিটার্ন: ইভেন্ট dict (না পেলে None)
    """
    raw = None
    for key in ('event_log', 'AccessControllerEvent', 'json'):
        if key in request.POST:
            raw = request.POST[key]
            break
        if key in request.FILES:
            raw = request.FILES[key].read()
            break
    if raw is None and 'multipart' not in (request.content_type or ''):
        raw = request.body
    if not raw:
        return None
    try:
        data = json.loads(raw if isinstance(raw, str) else raw.decode('utf-8', 'ignore'))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def punch_from_push(data):
    ev = dict(data.get('AccessControllerEvent') or {})
    ev.setdefault('dateTime', data.get('dateTime'))
    if _to_int(ev.get('subEventType') or ev.get('minor')) == MINOR_FACE_FAILED:
        return None     # ব্যর্থ যাচাই — হাজিরা নয় (stranger_from_push দেখে)
    return event_to_punch(ev)


def stranger_from_push(data):
    """অচেনা মুখ / মেলেনি এমন ইভেন্ট → (raw_id, সময়) — না হলে None"""
    ev = data.get('AccessControllerEvent') or {}
    minor = _to_int(ev.get('subEventType') or ev.get('minor'))
    emp_no = str(ev.get('employeeNoString') or ev.get('employeeNo') or '').strip()
    face = 'face' in str(ev.get('currentVerifyMode') or '').lower()
    if minor == MINOR_FACE_FAILED or (face and not emp_no and minor not in MINOR_METHOD):
        ts = parse_time(ev.get('dateTime') or data.get('dateTime'))
        return (emp_no, ts) if ts else None
    return None
