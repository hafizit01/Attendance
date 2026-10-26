from collections import defaultdict
from datetime import datetime, timedelta
import logging
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from zk import ZK

from attendance_app.models import Employee, Attendance
from attendance_app.services import create_employee_with_limit

logger = logging.getLogger(__name__)

DEBOUNCE_MINUTES = 5     # Out ধরতে প্রথম ও শেষ পাঞ্চের মধ্যে ন্যূনতম গ্যাপ
SYNC_OVERLAP_DAYS = 1    # শেষ সিঙ্কের দিনটা আবার দেখি, যাতে পরে আসা Out পাঞ্চ বাদ না পড়ে


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sync_start_date(department):
    """শেষ সফল সিঙ্কের (লোকাল) তারিখ − overlap; কখনো সিঙ্ক না হলে None (ডিভাইসের সব ডাটা)"""
    if not department.last_synced_at:
        return None
    return timezone.localtime(department.last_synced_at).date() - timedelta(days=SYNC_OVERLAP_DAYS)


def _sync_users(users, department, company):
    """
    ডিভাইসের নতুন ইউজারদের Employee বানায় — কোম্পানির employee limit মেনে।
    রিটার্ন: (created, skipped_by_limit)
    """
    # অন্য ব্রাঞ্চে একই ID থাকলেও সেটা আলাদা মানুষ — শুধু এই ব্রাঞ্চে দেখি
    existing = set(Employee.id_scope(department).values_list('device_user_id', flat=True))
    new_users = []
    for u in users:
        uid = _to_int(u.user_id)
        if uid is not None and uid not in existing:
            existing.add(uid)
            new_users.append((uid, u.name))

    created = 0
    for i, (uid, name) in enumerate(new_users):
        try:
            create_employee_with_limit(
                company=company, name=name or f"User {uid}",
                device_user_id=uid, department=department,
            )
            created += 1
            logger.info(f"➕ New employee added: {name or uid}")
        except ValidationError as e:
            # limit শেষ (বা সাবস্ক্রিপশন নেই) — বাকিদের জন্য আর চেষ্টা করে লাভ নেই
            skipped = len(new_users) - i
            logger.warning(f"⚠️ {skipped} device user(s) not added for {company}: {'; '.join(e.messages)}")
            return created, skipped
    return created, 0


def _sync_attendance(attendances, department, since):
    """
    ডিভাইসের পাঞ্চ থেকে প্রতি employee প্রতি দিনে একটা In (প্রথম পাঞ্চ) ও একটা Out (শেষ পাঞ্চ) রাখে।
    ম্যানুয়ালি এডিট করা In/Out (যার সময় মেশিনের পাঞ্চে নেই) বদলায় না।
    সব কোয়েরি বাল্কে — employee বা দিন প্রতি আলাদা কোয়েরি নেই।
    রিটার্ন: (created, updated)
    """
    # 1) মেশিনের পাঞ্চ: {(device_uid, local_date): {local_ts, ...}}
    day_punches = defaultdict(set)
    for a in attendances:
        uid = _to_int(a.user_id)
        if uid is None or a.timestamp is None:
            continue
        t = a.timestamp.replace(microsecond=0)
        if timezone.is_naive(t):
            t = timezone.make_aware(t)  # ডিভাইসের সময় লোকাল (Asia/Dhaka) ধরা হয়
        t = timezone.localtime(t)
        if since and t.date() < since:
            continue
        day_punches[(uid, t.date())].add(t)
    if not day_punches:
        return 0, 0

    # 2) Employee (এই মেশিনের ব্রাঞ্চের) ও ওই সময়ের সব DB রেকর্ড একবারে
    company = department.company
    emp_by_uid = {
        e.device_user_id: e
        for e in Employee.id_scope(department).filter(device_user_id__in={uid for uid, _ in day_punches})
    }
    dates = [d for _, d in day_punches]
    existing = (Attendance.objects
                .filter(employee__in=list(emp_by_uid.values()),
                        timestamp__date__range=(min(dates), max(dates)))
                .only('id', 'employee_id', 'timestamp', 'status'))

    db_day = defaultdict(lambda: {'In': [], 'Out': []})
    taken = set()  # (employee_id, timestamp, status) — unique constraint এর মতো
    for r in existing:
        db_day[(r.employee_id, timezone.localtime(r.timestamp).date())][r.status].append(r)
        taken.add((r.employee_id, r.timestamp, r.status))

    to_create, to_update = [], []

    def set_punch(emp, current, machine_ts, punches, status):
        key = (emp.id, machine_ts, status)
        if key in taken:
            return  # একই সময়/স্ট্যাটাসের রেকর্ড আগেই আছে
        if current is None:
            to_create.append(Attendance(employee=emp, company=company, timestamp=machine_ts, status=status))
        elif current.timestamp in punches and current.timestamp != machine_ts:
            # মেশিনের ডাটা থেকেই আসা (ম্যানুয়াল এডিট নয়) — তাই আপডেট
            taken.discard((emp.id, current.timestamp, status))
            current.timestamp = machine_ts
            to_update.append(current)
        else:
            return
        taken.add(key)

    for (uid, day), punches in day_punches.items():
        emp = emp_by_uid.get(uid)
        if not emp:
            continue
        ordered = sorted(punches)
        first, last = ordered[0], ordered[-1]
        recs = db_day[(emp.id, day)]
        db_in = min(recs['In'], key=lambda r: r.timestamp) if recs['In'] else None
        db_out = max(recs['Out'], key=lambda r: r.timestamp) if recs['Out'] else None

        set_punch(emp, db_in, first, punches, 'In')
        # একটা মাত্র পাঞ্চ (বা খুব কাছাকাছি দুটো) Out হবে না
        if last != first and (last - first) >= timedelta(minutes=DEBOUNCE_MINUTES):
            set_punch(emp, db_out, last, punches, 'Out')

    with transaction.atomic():
        Attendance.objects.bulk_create(to_create, batch_size=500, ignore_conflicts=True)
        Attendance.objects.bulk_update(to_update, ['timestamp'], batch_size=500)
    return len(to_create), len(to_update)


def import_pushed(department, users, punches):
    """
    Push agent (push_agent.py) থেকে আসা ডাটা — মেশিনে সরাসরি সিঙ্কের মতোই নিয়মে বসায়।
    users: [{"uid": 1, "name": "..."}], punches: [{"uid": 1, "time": "YYYY-MM-DD HH:MM:SS"}] (মেশিনের লোকাল সময়)
    রিটার্ন: result dict
    """
    user_objs = [SimpleNamespace(user_id=u.get('uid'), name=u.get('name') or '')
                 for u in users if isinstance(u, dict)]
    punch_objs, skipped = [], 0
    for p in punches:
        try:
            punch_objs.append(SimpleNamespace(user_id=p.get('uid'),
                                              timestamp=datetime.strptime(p.get('time'), "%Y-%m-%d %H:%M:%S")))
        except (AttributeError, TypeError, ValueError):
            skipped += 1

    new_emps, limit_skipped = _sync_users(user_objs, department, department.company)
    created, updated = _sync_attendance(punch_objs, department, None)
    department.last_synced_at = timezone.now()
    department.save(update_fields=['last_synced_at'])
    return {'status': 'success', 'employees_added': new_emps, 'employees_not_added': limit_skipped,
            'created': created, 'updated': updated, 'skipped': skipped}


def import_attendance(devices):
    """
    devices: [{'ip': ..., 'port': ..., 'department': Department}, ...]
    প্রতি ডিভাইসের জন্য result dict রিটার্ন করে।
    """
    results = []

    for device in devices:
        ip = device.get('ip')
        port = device.get('port')
        department = device.get('department')
        company = getattr(department, 'company', None)

        zk = ZK(ip, port=port, timeout=10, force_udp=False, ommit_ping=True)
        conn = None

        try:
            logger.info(f"🔌 Connecting to device {ip}:{port} for department {department.name}")
            conn = zk.connect()
            conn.disable_device()

            new_emps, limit_skipped = _sync_users(conn.get_users(), department, company)

            attendances = conn.get_attendance()
            if not attendances:
                raise Exception("No attendance data received or device offline.")

            since = _sync_start_date(department)
            created_count, updated_count = _sync_attendance(attendances, department, since)

            department.last_synced_at = timezone.now()
            department.save(update_fields=['last_synced_at'])

            message = f"✔️ Synced {created_count} new, Updated {updated_count} records."
            if new_emps:
                message += f" Added {new_emps} employee(s)."
            if limit_skipped:
                message += f" ⚠️ {limit_skipped} device user(s) not added (employee limit reached)."
            results.append({
                'department': department.name,
                'status': 'success',
                'message': message,
            })

        except Exception as e:
            logger.error(f"❌ Failed to sync {department.name}: {e}")
            results.append({
                'department': department.name,
                'status': 'error',
                'message': str(e)
            })

        finally:
            if conn:
                try:
                    conn.enable_device()
                    conn.disconnect()
                except Exception:
                    pass

    return results
