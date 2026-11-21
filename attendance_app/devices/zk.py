"""
ZKTeco / eSSL / ZK-compatible মেশিন — LAN এ সরাসরি কানেক্ট (pyzk, পোর্ট 4370)।
ফিঙ্গার, ফেস (iFace, MB160/360/460, SpeedFace এর অনেক মডেল), কার্ড, পাম — সব পাঞ্চ এক প্রোটোকলে আসে।
"""
import logging
from contextlib import contextmanager

from django.utils import timezone

from attendance_app.utils.zk_import import _sync_start_date, _to_int
from .ingest import Punch, ingest, method_from_zk

logger = logging.getLogger(__name__)


@contextmanager
def connect(device, timeout=15):
    from zk import ZK  # pyzk — শুধু LAN মেশিনে লাগে
    zk = ZK(device.ip, port=device.port or 4370, timeout=timeout, password=device.comm_key or 0,
            force_udp=False, ommit_ping=True)
    conn = zk.connect()
    try:
        yield conn
    finally:
        try:
            conn.enable_device()
        except Exception:
            pass
        try:
            conn.disconnect()
        except Exception:
            pass


def _read_info(conn, device):
    """মেশিনের মডেল/ফার্মওয়্যার/কতজন ইউজার-ফিঙ্গার-ফেস — না পেলে চুপচাপ বাদ"""
    fields = {}
    for attr, getter in (('model_name', 'get_device_name'), ('firmware', 'get_firmware_version'),
                         ('serial_number', 'get_serialnumber')):
        try:
            val = (getattr(conn, getter)() or '').strip()
            if val and not (attr == 'serial_number' and device.serial_number):
                fields[attr] = val[:100]
        except Exception:
            pass
    try:
        conn.read_sizes()
        fields.update(user_count=conn.users, finger_count=conn.fingers, record_count=conn.records,
                      face_count=getattr(conn, 'faces', None))
    except Exception:
        pass
    if 'serial_number' in fields:
        # অন্য মেশিনে একই SN থাকলে (ভুল কপি) বসাই না
        from attendance_app.models import Device
        if Device.objects.filter(serial_number=fields['serial_number']).exclude(pk=device.pk).exists():
            fields.pop('serial_number')
    return fields


def sync(device, full=False):
    """মেশিন থেকে ইউজার + পাঞ্চ এনে বসায়। full=True হলে মেশিনের সব পুরনো ডাটাও।"""
    department = device.department
    with connect(device) as conn:
        conn.disable_device()
        info = _read_info(conn, device)
        users = conn.get_users() or []
        records = conn.get_attendance() or []

    since = None if full else _sync_start_date_for(device)
    punches = []
    for a in records:
        uid = _to_int(a.user_id)
        if uid is None or a.timestamp is None:
            continue
        ts = a.timestamp
        if since and ts.date() < since:
            continue
        punches.append(Punch(uid=uid, timestamp=ts, method=method_from_zk(a.status),
                             verify_code=_to_int(a.status), state=_to_int(getattr(a, 'punch', None))))

    res = ingest(department, punches, users=users, device=device, source='pull')
    for k, v in info.items():
        setattr(device, k, v)
    device.last_seen_at = timezone.now()
    device.last_error = ''
    device.save()
    return res


def _sync_start_date_for(device):
    if device.last_synced_at:
        from datetime import timedelta
        from attendance_app.utils.zk_import import SYNC_OVERLAP_DAYS
        return timezone.localtime(device.last_synced_at).date() - timedelta(days=SYNC_OVERLAP_DAYS)
    return _sync_start_date(device.department) if device.department.last_synced_at else None


def test(device):
    with connect(device, timeout=8) as conn:
        info = _read_info(conn, device)
        try:
            info['device_time'] = conn.get_time()
        except Exception:
            pass
    return info


def set_time(device):
    with connect(device) as conn:
        conn.set_time(timezone.localtime().replace(tzinfo=None))


def restart(device):
    from zk import ZK
    zk = ZK(device.ip, port=device.port or 4370, timeout=10, password=device.comm_key or 0, ommit_ping=True)
    conn = zk.connect()
    conn.restart()  # রিস্টার্টের পর কানেকশন নিজেই কেটে যায়


def push_users(device, employees):
    """
    সফটওয়্যারের কর্মচারীদের মেশিনে বসায় (ID + নাম)। তারপর মেশিনে গিয়ে শুধু ফেস/ফিঙ্গার enroll করলেই হবে।
    মেশিনে আগে থেকে থাকা ইউজারের নাম আপডেট হয়, ফিঙ্গার/ফেস টেমপ্লেট মুছে না।
    """
    added = updated = 0
    with connect(device, timeout=20) as conn:
        conn.disable_device()
        existing = {str(u.user_id): u for u in (conn.get_users() or [])}
        next_uid = max([u.uid for u in existing.values()] or [0]) + 1
        for emp in employees:
            pin = str(emp.device_user_id)
            name = (emp.name or '')[:24]
            cur = existing.get(pin)
            if cur:
                if cur.name != name:
                    conn.set_user(uid=cur.uid, name=name, privilege=cur.privilege, password=cur.password,
                                  group_id=cur.group_id, user_id=pin, card=cur.card)
                    updated += 1
            else:
                conn.set_user(uid=next_uid, name=name, privilege=0, password='', group_id='', user_id=pin, card=0)
                next_uid += 1
                added += 1
    return added, updated


def delete_user(device, device_user_id):
    with connect(device) as conn:
        conn.delete_user(user_id=str(device_user_id))
