"""প্রোটোকল অনুযায়ী মেশিনে কাজ চালানো — ভিউ ও ম্যানেজমেন্ট কমান্ড শুধু এটা ডাকে।"""
import logging
from datetime import timedelta

from django.utils import timezone

from attendance_app.models import Device, DeviceCommand, Employee
from . import adms, hikvision, zk
from .ingest import summary

logger = logging.getLogger(__name__)

ACTIONS = {
    'sync': 'Sync now',
    'sync_full': 'Fetch all data from device',
    'test': 'Test connection',
    'set_time': 'Sync device clock',
    'push_users': 'Send employees to device',
    'restart': 'Restart device',
    'info': 'Refresh device info',
}


class DeviceActionError(Exception):
    pass


def _lan_client(device):
    if device.protocol == Device.PROTOCOL_ZK_PULL:
        return zk
    if device.protocol in (Device.PROTOCOL_HIK_ISAPI, Device.PROTOCOL_HIK_PUSH) and device.ip:
        return hikvision
    return None


def available_actions(device):
    """এই মেশিনে কোন বাটনগুলো কাজ করবে"""
    p = device.protocol
    if p == Device.PROTOCOL_ZK_ADMS:
        return ['sync', 'sync_full', 'push_users', 'restart', 'info']
    if p == Device.PROTOCOL_ZK_PULL or p == Device.PROTOCOL_HIK_ISAPI:
        return ['sync', 'sync_full', 'test', 'set_time', 'push_users', 'restart']
    if p == Device.PROTOCOL_HIK_PUSH and device.ip:
        return ['test', 'set_time', 'push_users', 'restart']
    return []


def branch_employees(device):
    return (Employee.id_scope(device.department).filter(is_active=True)
            .order_by('device_user_id'))


def known_on_device(device, employees):
    """
    কোন কর্মচারী নিশ্চিতভাবে এই মেশিনে আগে থেকেই আছে (device_user_id এর সেট):
      • এই মেশিন থেকে তার পাঞ্চ এসেছে, অথবা
      • ব্রাঞ্চে এটাই একমাত্র মেশিন আর তার মেশিনের হাজিরা আছে (হাতে দেওয়া/মোবাইল পাঞ্চ বাদে)
    ZKTeco cloud (ADMS) কমান্ডে Pri/Password/Card সব একসাথে লেখা হয় — আগে থেকে থাকা ইউজারকে আবার পাঠালে
    তার admin ক্ষমতা, পাসওয়ার্ড ও কার্ড নম্বর মুছে যেত। তাই এদের আর পাঠানো হয় না।
    """
    from attendance_app.models import Attendance, PunchLog
    ids = {e.device_user_id for e in employees}
    known = set(PunchLog.objects.filter(device=device, device_user_id__in=ids)
                .values_list('device_user_id', flat=True).distinct())
    branch_id = device.department.branch_id if device.department_id else None
    same_branch = Device.objects.filter(company_id=device.company_id, is_active=True)
    same_branch = (same_branch.filter(department__branch_id=branch_id) if branch_id
                   else same_branch.filter(department_id=device.department_id))
    if same_branch.exclude(pk=device.pk).count() == 0:
        emp_ids = [e.id for e in employees if e.device_user_id not in known]
        with_machine_punch = set(Attendance.objects.filter(employee_id__in=emp_ids, is_manual=False, is_remote_punch=False)
                                 .values_list('employee_id', flat=True).distinct())
        known |= {e.device_user_id for e in employees if e.id in with_machine_punch}
    return known


def queue(device, command, label='', user=None):
    return DeviceCommand.objects.create(device=device, command=command, label=label[:120], created_by=user)


def sync_device(device, full=False):
    """LAN মেশিন থেকে ডাটা আনা। রিটার্ন: {'status', 'message', ...}"""
    client = _lan_client(device) if device.is_pull else None
    if client is None:
        raise DeviceActionError("এই মেশিন নিজে সার্ভারে ডাটা পাঠায় — সিঙ্ক বাটন লাগে না।")
    if not device.ip:
        raise DeviceActionError("IP address সেট করা নেই।")
    try:
        res = client.sync(device, full=full)
    except Exception as e:
        device.last_error = str(e)[:500]
        device.save(update_fields=['last_error'])
        logger.warning("Device %s sync failed: %s", device.pk, e)
        raise DeviceActionError(_friendly(e)) from e
    res['message'] = summary(res)
    return res


def _friendly(e):
    text = str(e) or e.__class__.__name__
    low = text.lower()
    if 'timed out' in low or 'timeout' in low or "can't reach" in low or 'unreachable' in low:
        return f"মেশিনে কানেক্ট করা যায়নি (IP/Port ঠিক আছে কিনা, মেশিন চালু ও একই নেটওয়ার্কে কিনা দেখুন)। [{text}]"
    if 'unauth' in low or 'password' in low:
        return f"মেশিন পাসওয়ার্ড/Comm key মেলেনি। [{text}]"
    return text


def run_action(device, action, user=None):
    """একটা বাটনের কাজ — রিটার্ন: সফল হলে মেসেজ, না হলে DeviceActionError"""
    if action not in available_actions(device):
        raise DeviceActionError("এই মেশিনে এই কাজটা করা যায় না।")

    if device.protocol == Device.PROTOCOL_ZK_ADMS:
        now = timezone.localtime().replace(tzinfo=None)
        if action == 'sync':
            queue(device, adms.cmd_query_attlog(now - timedelta(days=2), now), 'Re-send last 2 days', user)
            return "মেশিনকে গত ২ দিনের ডাটা আবার পাঠাতে বলা হয়েছে — মেশিন পরের বার যোগাযোগ করলেই আসবে (সাধারণত ১ মিনিটের মধ্যে)।"
        if action == 'sync_full':
            device.adms_att_stamp = ''
            device.save(update_fields=['adms_att_stamp'])
            queue(device, adms.cmd_query_attlog(now - timedelta(days=365), now), 'Re-send all logs (1 year)', user)
            return "মেশিনকে সব পুরনো ডাটা আবার পাঠাতে বলা হয়েছে। অনেক ডাটা থাকলে কয়েক মিনিট লাগতে পারে।"
        if action == 'push_users':
            # শুধু নতুনদের পাঠাই — মেশিনে আগে থেকে থাকা কারো admin/পাসওয়ার্ড/কার্ড যেন না বদলায়
            all_emps = list(branch_employees(device))
            known = known_on_device(device, all_emps)
            emps = [e for e in all_emps if e.device_user_id not in known]
            DeviceCommand.objects.bulk_create([
                DeviceCommand(device=device, command=adms.cmd_upsert_user(e), label=f"Add user {e.device_user_id} · {e.name}"[:120],
                              created_by=user) for e in emps
            ])
            skipped = len(all_emps) - len(emps)
            note = f" {skipped} জন আগে থেকেই মেশিনে আছেন — তাদের কিছু বদলানো হয়নি।" if skipped else ''
            if not emps:
                return "সবাই আগে থেকেই মেশিনে আছেন — কিছু পাঠানো হয়নি, মেশিনের কিছু বদলায়নি।"
            return f"{len(emps)} জন নতুন কর্মচারী মেশিনে পাঠানোর জন্য সারিতে রাখা হয়েছে।{note} এরপর মেশিনে গিয়ে ফেস/ফিঙ্গার enroll করুন।"
        if action == 'restart':
            queue(device, adms.CMD_REBOOT, 'Restart', user)
            return "রিস্টার্ট কমান্ড পাঠানো হয়েছে।"
        if action == 'info':
            queue(device, adms.CMD_INFO, 'Refresh info', user)
            return "মেশিনের তথ্য চাওয়া হয়েছে।"

    client = _lan_client(device)
    if client is None or not device.ip:
        raise DeviceActionError("IP address সেট করা নেই।")
    if action in ('sync', 'sync_full'):
        return sync_device(device, full=(action == 'sync_full'))['message']
    try:
        if action == 'test':
            info = client.test(device)
            for k in ('model_name', 'firmware'):
                if info.get(k):
                    setattr(device, k, info[k])
            for k in ('user_count', 'finger_count', 'face_count', 'record_count'):
                if info.get(k) is not None:
                    setattr(device, k, info[k])
            device.last_seen_at, device.last_error = timezone.now(), ''
            device.save()
            parts = [f"{k.replace('_', ' ')}: {v}" for k, v in info.items() if v not in (None, '')]
            return "✔️ Connected. " + ", ".join(parts)
        if action == 'set_time':
            client.set_time(device)
            return f"মেশিনের ঘড়ি সার্ভারের সময়ে ({timezone.localtime():%d %b %Y %I:%M %p}) সেট করা হয়েছে।"
        if action == 'restart':
            client.restart(device)
            return "মেশিন রিস্টার্ট হচ্ছে।"
        if action == 'push_users':
            added, updated = client.push_users(device, list(branch_employees(device)))
            if client is hikvision:
                return (f"মেশিনে {added} জন নতুন যোগ হয়েছে। {updated} জন আগে থেকেই ছিলেন — তাদের কিছু বদলানো হয়নি। "
                        "এখন মেশিনে নতুনদের ফেস/কার্ড enroll করুন।")
            return (f"মেশিনে {added} জন নতুন যোগ, {updated} জনের শুধু নাম আপডেট (admin/পাসওয়ার্ড/কার্ড/ফিঙ্গার/ফেস যেমন ছিল তেমনই)। "
                    "এখন মেশিনে নতুনদের ফেস/ফিঙ্গার enroll করুন।")
    except DeviceActionError:
        raise
    except Exception as e:
        device.last_error = str(e)[:500]
        device.save(update_fields=['last_error'])
        raise DeviceActionError(_friendly(e)) from e
    raise DeviceActionError("Unknown action")
