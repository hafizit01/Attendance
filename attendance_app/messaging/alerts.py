"""
অ্যালার্ট:
  • daily_absence_alerts(): প্রতিটা ডিপার্টমেন্টের ম্যানেজারকে আজকের Absent/Late তালিকা (দিনে একবার, নির্ধারিত সময়ের পর)
  • salary_disbursed(): কর্মচারীদের "বেতন দেওয়া হয়েছে" + পে-স্লিপের নিরাপদ লিংক
হাজিরার হিসাব attendance_helpers থেকে — রিপোর্টের সাথে একই সংখ্যা।
"""
import logging
from datetime import datetime

from django.core import signing
from django.urls import reverse
from django.utils import timezone

from attendance_app.models import Company, Department, Employee, HRSettings
from attendance_app.utils import attendance_helpers as att_rules
from .service import send_message

logger = logging.getLogger(__name__)

PAYSLIP_LINK_SALT = 'payslip-link'
PAYSLIP_LINK_MAX_AGE = 60 * 60 * 24 * 45   # ৪৫ দিন
SMS_MAX = 600


# ---------------------------------------------------------
# পে-স্লিপের নিরাপদ লিংক (লগইন ছাড়া — শুধু ঐ কর্মচারী ও ঐ মাস, মেয়াদসহ)
# ---------------------------------------------------------
def payslip_token(employee, month_str):
    return signing.TimestampSigner(salt=PAYSLIP_LINK_SALT).sign(f"{employee.id}.{month_str}")


def read_payslip_token(token):
    """রিটার্ন: (employee_id, month_str) — ভুল/মেয়াদোত্তীর্ণ হলে signing.BadSignature"""
    value = signing.TimestampSigner(salt=PAYSLIP_LINK_SALT).unsign(token, max_age=PAYSLIP_LINK_MAX_AGE)
    emp_id, month = value.split('.', 1)
    return int(emp_id), month


def payslip_link(employee, month_str, base_url):
    return base_url.rstrip('/') + reverse('public_payslip', args=[payslip_token(employee, month_str)])


def employee_phone(employee):
    profile = getattr(employee, 'profile', None)   # userapp.EmployeeProfile
    return (getattr(profile, 'mobile_number', '') or '').strip()


# ---------------------------------------------------------
# প্রতিদিনের Absent / Late
# ---------------------------------------------------------
def _fmt_minutes(td):
    m = int(td.total_seconds() // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def department_today(department, day):
    """(absent_names, late [(name, td)]) — শুধু আজ যাদের কর্মদিবস"""
    emps = list(Employee.objects.filter(department=department, is_active=True)
                .select_related('department', 'department__branch', 'department__default_shift').order_by('name'))
    absent, late = [], []
    for emp, _s, days in att_rules.build_attendance(emps, day, day):
        d = days[0]
        if d['status'] == 'Absent':
            absent.append(emp.name)
        elif d['late'] and d['late'].total_seconds() > 0 and not (d['is_holiday'] or d['is_off_day']):
            late.append((emp.name, d['late']))
    return absent, late


def build_daily_text(company, department, day, absent, late):
    head = f"{company.name} · {department.name} · {day:%d %b}"
    parts = [head]
    parts.append(f"Absent ({len(absent)}): " + (", ".join(absent) if absent else "none"))
    parts.append(f"Late ({len(late)}): " + (", ".join(f"{n} {_fmt_minutes(t)}" for n, t in late) if late else "none"))
    text = "\n".join(parts)
    return text if len(text) <= SMS_MAX else text[:SMS_MAX - 1] + "…"


def daily_absence_alerts(now=None, company_ids=None, force=False):
    """
    যাদের HR settings এ চালু ও সময় হয়ে গেছে, প্রতি ডিপার্টমেন্টে দিনে একটাই বার্তা (dedupe)।
    force=True: সময়/সেটিং না দেখে এখনই (পরীক্ষার জন্য)। রিটার্ন: {company: [status, ...]}
    """
    now = timezone.localtime(now or timezone.now())
    day = now.date()
    out = {}
    qs = Company.objects.all()
    if company_ids:
        qs = qs.filter(id__in=company_ids)
    for company in qs:
        hr = HRSettings.for_company(company)
        if not force and (not hr.enable_daily_absence_alert or now.time() < hr.daily_alert_time):
            continue
        results = []
        for dept in Department.objects.filter(company=company).exclude(manager_phone=''):
            absent, late = department_today(dept, day)
            if not absent and not late:
                continue
            log = send_message(company, dept.manager_phone, build_daily_text(company, dept, day, absent, late),
                               kind='daily_alert', channel=hr.alert_channel,
                               dedupe_key=None if force else f"daily:{dept.id}:{day:%Y%m%d}")
            results.append(log.status)
        out[company.name] = results
    return out


# ---------------------------------------------------------
# বেতন দেওয়ার SMS
# ---------------------------------------------------------
def salary_disbursed(company, month_str, base_url, employee_ids=None):
    """
    মাসের পে-স্লিপ আছে এমন প্রত্যেক কর্মচারীকে (যার মোবাইল নম্বর আছে) একবার।
    রিটার্ন: dict(sent, logged, failed, no_phone, skipped)
    """
    from attendance_app.portal.services import payslip
    hr = HRSettings.for_company(company)
    month_label = datetime.strptime(month_str, '%Y-%m').strftime('%B %Y')
    qs = Employee.objects.filter(company=company, is_active=True, employeesalary__isnull=False).select_related('profile')
    if employee_ids:
        qs = qs.filter(id__in=employee_ids)
    counts = {'sent': 0, 'logged': 0, 'failed': 0, 'no_phone': 0, 'skipped': 0}
    for emp in qs:
        phone = employee_phone(emp)
        if not phone:
            counts['no_phone'] += 1
            continue
        slip = payslip(emp, month_str)
        if slip is None:
            counts['skipped'] += 1
            continue
        text = (f"{company.name}: {emp.name}, your salary for {month_label} has been disbursed. "
                f"Net ৳{slip['final_salary']:,.0f}. Payslip: {payslip_link(emp, month_str, base_url)}")
        log = send_message(company, phone, text, kind='salary', channel=hr.alert_channel,
                           dedupe_key=f"salary:{emp.id}:{month_str}")
        counts[log.status] = counts.get(log.status, 0) + 1
    return counts
