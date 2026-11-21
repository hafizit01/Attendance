"""
ডিপার্টমেন্ট ম্যানেজারদের প্রতিদিনের Absent/Late SMS/WhatsApp।
Celery Beat না থাকলে প্রতি ১৫ মিনিটে cron / Task Scheduler দিয়ে চালান — যাদের সময় হয়েছে শুধু তাদের, দিনে একবার:
    */15 * * * *  cd /home/USER/app && python manage.py send_attendance_alerts
"""
from django.core.management.base import BaseCommand

from attendance_app.messaging.alerts import daily_absence_alerts


class Command(BaseCommand):
    help = "HR settings এ চালু থাকলে ও সময় হলে ডিপার্টমেন্ট ম্যানেজারদের আজকের Absent/Late তালিকা পাঠায়।"

    def add_arguments(self, parser):
        parser.add_argument("--company", type=int, action="append", dest="companies")
        parser.add_argument("--force", action="store_true", help="সময়/সেটিং না দেখে এখনই পাঠাও (পরীক্ষা)")

    def handle(self, *args, companies=None, force=False, **options):
        results = daily_absence_alerts(company_ids=companies, force=force)
        if not results:
            self.stdout.write("এখন পাঠানোর কিছু নেই (সেটিং বন্ধ বা সময় হয়নি)।")
        for name, statuses in results.items():
            self.stdout.write(f"{name}: {len(statuses)} message(s) — " + (", ".join(statuses) or "nobody absent/late"))
