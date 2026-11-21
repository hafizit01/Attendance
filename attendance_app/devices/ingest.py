"""
সব ধরনের মেশিনের পাঞ্চ এখান দিয়ে ঢোকে:
  1) মেশিনের ইউজার → Employee (কোম্পানির limit মেনে)
  2) প্রতিটা পাঞ্চ PunchLog এ (কোন মেশিন, ফেস/ফিঙ্গার/কার্ড)
  3) যেসব (কর্মচারী, দিন) এ নতুন পাঞ্চ এসেছে, সেই দিনের সব পাঞ্চ থেকে In (প্রথম) / Out (শেষ) আবার হিসাব

৩ নম্বর ধাপের জন্য রিয়েল-টাইম মেশিন (ADMS/Hikvision) একটা একটা পাঞ্চ পাঠালেও Out ঠিকমতো বসে।
"""
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace

from django.utils import timezone

from attendance_app.models import Employee, PunchLog
from attendance_app.utils.zk_import import _sync_attendance, _sync_users, _to_int

logger = logging.getLogger(__name__)

# ZKTeco verify code → পদ্ধতি (pyzk এর `status`, ADMS ATTLOG এর ৪র্থ কলাম)
ZK_VERIFY = {0: 'password', 1: 'finger', 2: 'card', 3: 'password', 4: 'card', 15: 'face', 25: 'palm'}
METHOD_ALIASES = {
    'face': 'face', 'finger': 'finger', 'fingerprint': 'finger', 'fp': 'finger', 'card': 'card',
    'rfid': 'card', 'palm': 'palm', 'password': 'password', 'pin': 'password', 'pwd': 'password',
}


def method_from_zk(code):
    code = _to_int(code)
    if code is None:
        return 'other'
    return ZK_VERIFY.get(code, 'other')


def method_from_text(text):
    """'face', 'fingerPrint', 'cardOrFace' ইত্যাদি থেকে মূল পদ্ধতি"""
    t = (str(text or '')).strip().lower()
    if not t:
        return 'other'
    if t in METHOD_ALIASES:
        return METHOD_ALIASES[t]
    for key in ('face', 'palm', 'finger', 'card', 'password'):
        if key in t:
            return key
    return 'other'


@dataclass
class Punch:
    uid: int
    timestamp: datetime          # aware বা naive (naive = লোকাল সময়)
    method: str = 'other'
    verify_code: int = None
    state: int = None


def _aware(ts):
    ts = ts.replace(microsecond=0)
    if timezone.is_naive(ts):
        ts = timezone.make_aware(ts)
    return ts


def _rename_placeholders(department, users):
    """আগে 'User 5' নামে বানানো কর্মচারী — মেশিন এখন আসল নাম দিলে বসিয়ে দাও"""
    named = {}
    for u in users:
        uid, name = _to_int(u.user_id), (u.name or '').strip()
        if uid is not None and name:
            named[uid] = name
    if not named:
        return
    for emp in Employee.id_scope(department).filter(device_user_id__in=list(named)):
        if emp.name == f"User {emp.device_user_id}" and named[emp.device_user_id] != emp.name:
            emp.name = named[emp.device_user_id]
            emp.save(update_fields=['name'])


def ingest(department, punches, *, users=(), device=None, source='pull', create_unknown=False):
    """
    department: যে ডিপার্টমেন্টের মেশিন (ব্রাঞ্চ ঠিক করে ID কার)
    punches: Punch এর লিস্ট
    users: [obj(user_id, name)] — মেশিনের ইউজার লিস্ট (থাকলে)
    create_unknown: পাঞ্চে অচেনা ID থাকলে 'User N' নামে কর্মচারী বানাও। None = HR settings
                    (auto_create_unknown_ids) অনুযায়ী। না বানালে অচেনা পাঞ্চ UnknownPunch এ জমা ও অ্যাডমিন সতর্কবার্তা।
    """
    company = department.company
    if create_unknown is None:
        from attendance_app.models import HRSettings
        create_unknown = HRSettings.for_company(company).auto_create_unknown_ids
    users = [u for u in users if u is not None]
    added, not_added = _sync_users(users, department, company) if users else (0, 0)
    if users:
        _rename_placeholders(department, users)

    punches = [p for p in punches if p.uid is not None and p.timestamp is not None]
    result = {'status': 'success', 'employees_added': added, 'employees_not_added': not_added,
              'received': len(punches), 'logged': 0, 'created': 0, 'updated': 0, 'unknown': 0}
    if not punches:
        return result

    if create_unknown:
        known = set(Employee.id_scope(department).values_list('device_user_id', flat=True))
        unknown = sorted({p.uid for p in punches} - known)
        if unknown:
            a, s = _sync_users([SimpleNamespace(user_id=u, name='') for u in unknown], department, company)
            result['employees_added'] += a
            result['employees_not_added'] += s

    uids = {p.uid for p in punches}
    emp_by_uid = {e.device_user_id: e for e in Employee.id_scope(department).filter(device_user_id__in=uids)}
    unknown_uids = uids - set(emp_by_uid)
    result['unknown'] = len(unknown_uids)
    if unknown_uids:
        from attendance_app.hr import unknown as unknown_punches
        unknown_punches.record(company, [(p.uid, p.timestamp, p.method) for p in punches if p.uid in unknown_uids],
                               device=device, department=department)

    branch_id = department.branch_id
    scope_key = PunchLog.make_scope_key(company.id, branch_id)
    logs = []
    for p in punches:
        emp = emp_by_uid.get(p.uid)
        logs.append(PunchLog(
            company=company, branch_id=branch_id, department=department, device=device,
            employee=emp, device_user_id=p.uid, timestamp=_aware(p.timestamp),
            method=p.method or 'other', verify_code=p.verify_code, punch_state=p.state,
            source=source, scope_key=scope_key,
        ))
    before = PunchLog.objects.filter(scope_key=scope_key).count()
    PunchLog.objects.bulk_create(logs, batch_size=1000, ignore_conflicts=True)
    result['logged'] = PunchLog.objects.filter(scope_key=scope_key).count() - before

    # যেসব (ID, দিন) এ নতুন পাঞ্চ — সেই দিনগুলোর (এই ব্রাঞ্চের সব মেশিনের) পুরো পাঞ্চ থেকে In/Out
    wanted = defaultdict(set)
    for p in punches:
        if p.uid in emp_by_uid:
            wanted[p.uid].add(timezone.localtime(_aware(p.timestamp)).date())

    # আগে অচেনা ছিল, এখন কর্মচারী আছে — পুরনো লগে employee বসাও, ওই দিনগুলোর হাজিরাও বানাও
    if emp_by_uid:
        orphans = PunchLog.objects.filter(scope_key=scope_key, employee__isnull=True,
                                          device_user_id__in=list(emp_by_uid))
        for uid, ts in orphans.values_list('device_user_id', 'timestamp'):
            wanted[uid].add(timezone.localtime(ts).date())
        for uid in {u for u, _ in orphans.values_list('device_user_id', 'timestamp')}:
            orphans.filter(device_user_id=uid).update(employee=emp_by_uid[uid])

    days = {d for ds in wanted.values() for d in ds}
    if days:
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(min(days), datetime.min.time()), tz)
        end = timezone.make_aware(datetime.combine(max(days) + timedelta(days=1), datetime.min.time()), tz)
        day_rows = (PunchLog.objects
                    .filter(scope_key=scope_key, device_user_id__in=list(wanted),
                            timestamp__gte=start, timestamp__lt=end)
                    .values_list('device_user_id', 'timestamp'))
        full = [SimpleNamespace(user_id=uid, timestamp=ts) for uid, ts in day_rows
                if timezone.localtime(ts).date() in wanted[uid]]
        created, updated = _sync_attendance(full, department, None)
        result['created'], result['updated'] = created, updated

    now = timezone.now()
    department.last_synced_at = now
    department.save(update_fields=['last_synced_at'])
    if device is not None:
        device.last_synced_at = now
        device.save(update_fields=['last_synced_at'])
    return result


def summary(res):
    msg = f"{res.get('created', 0)} new, {res.get('updated', 0)} updated attendance · {res.get('logged', 0)} punches logged"
    if res.get('employees_added'):
        msg += f" · {res['employees_added']} employee(s) added"
    if res.get('employees_not_added'):
        msg += f" · ⚠️ {res['employees_not_added']} not added (employee limit)"
    if res.get('unknown'):
        msg += f" · {res['unknown']} unknown ID(s)"
    return msg
