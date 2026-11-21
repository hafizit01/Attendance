"""
অচেনা পাঞ্চ: মেশিনের ID কোনো কর্মচারীর সাথে মেলে না, অথবা Hikvision বলছে অচেনা মুখ।
UnknownPunch এ জমা হয় আর HR অ্যাডমিনরা সাথে সাথে নোটিফিকেশন পান (হেডারের ঘণ্টা + পপ-আপ)।
নোটিফিকেশন জমে যাওয়া রোধে: এক সিঙ্ক/রিকোয়েস্টে একটাই সারাংশ, আর শুধু গত ২৪ ঘণ্টার পাঞ্চ হলে
(প্রথমবার পুরো মেশিনের পুরনো ডাটা পড়লে পুরনো অচেনা পাঞ্চ লগ হয়, কিন্তু নোটিফিকেশন যায় না)।
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from attendance_app.models import Notification, UnknownPunch

NOTIFY_WINDOW = timedelta(hours=24)


def _aware(ts):
    ts = ts.replace(microsecond=0)
    return timezone.make_aware(ts) if timezone.is_naive(ts) else ts


def record(company, items, *, device=None, department=None, reason='unmapped'):
    """
    items: [(raw_id, timestamp, method)]। রিটার্ন: নতুন জমা হওয়া UnknownPunch লিস্ট
    """
    if not items:
        return []
    rows = [UnknownPunch(company=company, device=device, department=department, raw_id=str(raw_id or '')[:64],
                         timestamp=_aware(ts), method=method or 'other', reason=reason) for raw_id, ts, method in items]
    keys = {(r.raw_id, r.timestamp) for r in rows}
    existing = set(UnknownPunch.objects.filter(company=company, reason=reason,
                                               timestamp__in=[t for _r, t in keys]).values_list('raw_id', 'timestamp'))
    new_rows = [r for r in rows if (r.raw_id, r.timestamp) not in existing]
    UnknownPunch.objects.bulk_create(new_rows, ignore_conflicts=True, batch_size=500)
    recent = [r for r in new_rows if timezone.now() - r.timestamp <= NOTIFY_WINDOW]
    if recent:
        notify(company, recent, device=device, reason=reason)
    return new_rows


def notify(company, rows, *, device=None, reason='unmapped'):
    from attendance_app.hr.missing_punch import _recipients
    ids = sorted({r.raw_id for r in rows if r.raw_id})
    where = f" ({device.name})" if device else ''
    if reason == 'stranger':
        title = f"অচেনা মুখ শনাক্ত{where}"
        msg = f"{len(rows)} বার অচেনা কেউ মেশিনে মুখ দেখিয়েছে — সর্বশেষ {timezone.localtime(rows[-1].timestamp):%d %b %I:%M %p}।"
    else:
        title = f"অচেনা ID থেকে পাঞ্চ{where}"
        msg = (f"ID {', '.join(ids[:8])}{' …' if len(ids) > 8 else ''} — সফটওয়্যারে এই ID এর কোনো কর্মচারী নেই। "
               f"কর্মচারী যোগ করুন বা উপেক্ষা করুন।")
    branch_id = device.department.branch_id if device and device.department_id else None
    link = reverse('attendance_app:unknown_punch_list')
    Notification.objects.bulk_create([
        Notification(company=company, user=u, kind='device', title=title, message=msg, link=link)
        for u, b in _recipients(company) if b is None or b == branch_id
    ])
