"""
Celery task (ঐচ্ছিক)। Celery ইনস্টল না থাকলে বা .env এ CELERY_ENABLED চালু না থাকলে কাজগুলো
সাথে সাথে (একই রিকোয়েস্টে) চলে — সফটওয়্যার আগের মতোই কাজ করে।
শিডিউল: attendance_project/celery.py।  Celery ছাড়া: cron দিয়ে management command।
"""
import os

try:
    from celery import shared_task
except ImportError:  # Celery নেই
    shared_task = None

from attendance_app.hr import missing_punch


def celery_enabled():
    return shared_task is not None and (os.getenv('CELERY_ENABLED', '') or '').strip().lower() in ('1', 'true', 'yes', 'on')


def check_missing_punches():
    """সব কোম্পানির (যাদের alert চালু) missing out-punch চেক — রিটার্ন: {company: result}"""
    return missing_punch.run()


def send_daily_attendance_alerts():
    """ডিপার্টমেন্ট ম্যানেজারদের আজকের Absent/Late (সময় হয়ে গেলে, দিনে একবার)"""
    from attendance_app.messaging.alerts import daily_absence_alerts
    return daily_absence_alerts()


def send_salary_disbursed(company_id, month_str, base_url):
    from attendance_app.messaging.alerts import salary_disbursed
    from attendance_app.models import Company
    return salary_disbursed(Company.objects.get(pk=company_id), month_str, base_url)


if shared_task is not None:
    check_missing_punches_task = shared_task(name='attendance_app.check_missing_punches')(check_missing_punches)
    send_daily_attendance_alerts_task = shared_task(name='attendance_app.send_daily_attendance_alerts')(send_daily_attendance_alerts)
    send_salary_disbursed_task = shared_task(name='attendance_app.send_salary_disbursed')(send_salary_disbursed)


def dispatch(name, *args):
    """Celery চালু থাকলে কিউতে পাঠায় (রিটার্ন None), নইলে এখনই চালিয়ে ফলাফল রিটার্ন করে"""
    fn = globals()[name]
    if celery_enabled():
        globals()[f"{name}_task"].delay(*args)
        return None
    return fn(*args)
