"""
Celery + Beat (ঐচ্ছিক)। চালাতে:
    pip install -r requirements-celery.txt          (celery + redis)
    .env এ:  CELERY_BROKER_URL=redis://localhost:6379/0
    celery -A attendance_project worker -l info
    celery -A attendance_project beat -l info

Celery ইনস্টল না থাকলে এই মডিউল import হয় না (attendance_project/__init__.py দেখুন), সফটওয়্যার আগের মতোই চলে।
"""
import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'attendance_project.settings')

app = Celery('attendance_project')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    # প্রতিদিন রাত ১১:৫৯ — In আছে কিন্তু Out নেই
    'check-missing-punches-nightly': {
        'task': 'attendance_app.check_missing_punches',
        'schedule': crontab(hour=23, minute=59),
    },
    # প্রতি ১৫ মিনিটে দেখে — যে কোম্পানির নির্ধারিত সময় পার হয়েছে তাদের ম্যানেজারকে দিনে একবার পাঠায়
    'daily-absent-late-alerts': {
        'task': 'attendance_app.send_daily_attendance_alerts',
        'schedule': crontab(minute='*/15'),
    },
}
