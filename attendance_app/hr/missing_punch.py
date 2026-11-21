"""
Missing Out-Punch চেক — রাত ১১:৫৯ এ চলে (Celery Beat / cron / Task Scheduler — সবাই এটাই ডাকে)।

নিয়ম (হাজিরার হিসাবের সাথে মিল রেখে, attendance_helpers.build_attendance):
  • দিনের শিফট: ঐ দিনে In আছে, Out নেই → Missing Out-Punch
  • রাতের শিফট: Out পরের দিন সকালে আসে, তাই আজকের রাতে নয় — আগের রাতের শিফট চেক হয়
  • আগের খোলা রেকর্ডে পরে Out এলে (মেশিন সিঙ্ক/ম্যানুয়াল এডিট) নিজে থেকেই Resolved
শুধু যেসব কোম্পানির HRSettings.enable_missing_punch_alert চালু।
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from attendance_app.models import Company, Employee, HRSettings, MissingPunch, Notification, UserProfile
from attendance_app.utils import attendance_helpers as att_rules

logger = logging.getLogger(__name__)

RECHECK_DAYS = 7   # এত দিন পুরনো খোলা রেকর্ড আবার মিলিয়ে দেখা হয় (পরে Out এলে resolved)


def _day_results(employees, start, end):
    """{(employee_id, date): day_result}"""
    out = {}
    for emp, _shift, days in att_rules.build_attendance(employees, start, end):
        for d in days:
            out[(emp.id, d['date'])] = d
    return out


def find_missing(company, day):
    """
    company র যাদের `day` তে In আছে কিন্তু Out নেই: [(employee, in_time, check_date)]
    রাতের শিফটের কর্মচারীর জন্য check_date = day − 1 (তাদের Out আজ সকালে)।
    """
    employees = list(Employee.objects.filter(company=company, is_active=True)
                     .select_related('department', 'department__branch', 'department__default_shift'))
    if not employees:
        return []
    prev = day - timedelta(days=1)
    results = _day_results(employees, prev, day)
    resolver = att_rules.ShiftResolver(employees, prev, day)
    missing = []
    for emp in employees:
        checks = []
        if not resolver.on(emp, day).night:
            checks.append(day)      # দিনের শিফট: আজকের Out আজই আসার কথা
        if resolver.on(emp, prev).night:
            checks.append(prev)     # গত রাতের শিফট: Out আজ সকালে আসার কথা ছিল
        for check in checks:
            d = results.get((emp.id, check))
            if d and d['in_time'] and not d['out_time'] and not d.get('is_elsewhere'):
                missing.append((emp, d['in_time'], check))
    return missing


def resolve_closed(company, since):
    """খোলা রেকর্ড যেগুলোর এখন Out আছে → resolved। রিটার্ন: কতগুলো"""
    open_qs = list(MissingPunch.objects.filter(company=company, status=MissingPunch.STATUS_OPEN, date__gte=since)
                   .select_related('employee', 'employee__department'))
    if not open_qs:
        return 0
    emps = {m.employee_id: m.employee for m in open_qs}
    start = min(m.date for m in open_qs)
    end = max(m.date for m in open_qs)
    results = _day_results(list(emps.values()), start, end)
    done = [m.pk for m in open_qs if (results.get((m.employee_id, m.date)) or {}).get('out_time')]
    if done:
        MissingPunch.objects.filter(pk__in=done).update(status=MissingPunch.STATUS_RESOLVED, resolved_at=timezone.now())
    return len(done)


def _recipients(company):
    """
    কারা নোটিফিকেশন পাবে: কোম্পানির মালিক/অ্যাডমিন (সব ব্রাঞ্চ) ও ব্রাঞ্চ ম্যানেজার (নিজের ব্রাঞ্চ)।
    রিটার্ন: [(user, branch_id বা None)]
    """
    seen, out = set(), []
    for p in UserProfile.objects.filter(company=company).select_related('user'):
        if p.user.is_active and p.user_id not in seen:
            seen.add(p.user_id)
            out.append((p.user, p.branch_id))
    if company.owner_id and company.owner_id not in seen and company.owner.is_active:
        out.append((company.owner, None))
    return out


def notify(company, created):
    """নতুন missing punch গুলোর জন্য প্রতি প্রাপকে একটা সারাংশ নোটিফিকেশন"""
    if not created:
        return 0
    by_branch = defaultdict(list)
    for m in created:
        dept = m.employee.department
        by_branch[dept.branch_id if dept else None].append(m)
    link = reverse('attendance_app:missing_punch_list')
    notes = []
    for user, branch_id in _recipients(company):
        items = created if branch_id is None else by_branch.get(branch_id, [])
        if not items:
            continue
        names = ", ".join(m.employee.name for m in items[:5]) + (f" এবং আরও {len(items) - 5} জন" if len(items) > 5 else "")
        day = items[0].date
        notes.append(Notification(
            company=company, user=user, kind='missing_punch',
            title=f"{len(items)} জনের Out-punch নেই ({day:%d %b})",
            message=f"{names} — In দিয়েছে কিন্তু Out দেয়নি। হাজিরা ঠিক করুন বা কর্মচারীকে জিজ্ঞেস করুন।",
            link=link,
        ))
    Notification.objects.bulk_create(notes)
    return len(notes)


def check_company(company, day=None):
    """একটা কোম্পানির চেক। রিটার্ন: dict(found, created, resolved, notified)"""
    day = day or timezone.localdate()
    resolved = resolve_closed(company, day - timedelta(days=RECHECK_DAYS))
    found = find_missing(company, day)
    created = []
    with transaction.atomic():
        existing = set(MissingPunch.objects.filter(company=company, employee_id__in=[e.id for e, _t, _d in found])
                       .values_list('employee_id', 'date'))
        for emp, in_time, check_date in found:
            if (emp.id, check_date) in existing:
                continue
            created.append(MissingPunch.objects.create(company=company, employee=emp, date=check_date, in_time=in_time))
        notified = notify(company, created)
    return {'found': len(found), 'created': len(created), 'resolved': resolved, 'notified': notified}


def run(day=None, company_ids=None, force=False):
    """
    সব কোম্পানি (যাদের alert চালু; force=True হলে সেটিং যাই হোক)।
    রিটার্ন: {company_name: result}
    """
    qs = Company.objects.all()
    if company_ids:
        qs = qs.filter(id__in=company_ids)
    out = {}
    for company in qs:
        if not force and not HRSettings.for_company(company).enable_missing_punch_alert:
            continue
        try:
            out[company.name] = check_company(company, day)
        except Exception as e:  # এক কোম্পানির সমস্যায় বাকিরা আটকাবে না
            logger.exception("Missing punch check failed for company %s", company.pk)
            out[company.name] = {'error': str(e)}
    return out
