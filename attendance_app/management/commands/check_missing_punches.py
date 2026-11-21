"""
Missing Out-Punch চেক।  প্রতিদিন রাত ১১:৫৯ এ চালান:
  • Celery Beat থাকলে: attendance_project/celery.py তে নিজে থেকেই শিডিউল করা
  • না থাকলে: run_missing_punch_check.bat (Windows Task Scheduler) বা cPanel cron:
        59 23 * * *  cd /home/USER/app && python manage.py check_missing_punches
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from attendance_app.hr import missing_punch


class Command(BaseCommand):
    help = "যাদের In আছে কিন্তু Out নেই তাদের Missing Out-Punch হিসেবে চিহ্নিত করে নোটিফিকেশন পাঠায় (HR settings এ চালু থাকলে)।"

    def add_arguments(self, parser):
        parser.add_argument("--date", help="এই তারিখ চেক করো (YYYY-MM-DD); ডিফল্ট আজ")
        parser.add_argument("--company", type=int, action="append", dest="companies", help="শুধু এই কোম্পানি id")
        parser.add_argument("--force", action="store_true", help="HR settings এ বন্ধ থাকলেও চালাও")

    def handle(self, *args, date=None, companies=None, force=False, **options):
        day = None
        if date:
            try:
                day = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD")
        results = missing_punch.run(day=day, company_ids=companies, force=force)
        if not results:
            self.stdout.write(self.style.WARNING("কোনো কোম্পানিতে Missing punch alert চালু নেই (HR Settings)।"))
            return
        failed = 0
        for name, res in results.items():
            if 'error' in res:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{name}: {res['error']}"))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"{name}: {res['created']} new missing out-punch, {res['resolved']} resolved, "
                    f"{res['notified']} notification(s)"))
        if failed:
            raise CommandError(f"{failed} company check(s) failed.")
