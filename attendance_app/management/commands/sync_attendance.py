from django.core.management.base import BaseCommand, CommandError

from attendance_app.models import Department
from attendance_app.utils.zk_import import import_attendance


class Command(BaseCommand):
    help = "ZKTeco ডিভাইস থেকে হাজিরা সিঙ্ক করে (device IP/Port সেট করা সব ডিপার্টমেন্ট, অথবা --department দিয়ে নির্দিষ্টগুলো)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--department", type=int, action="append", dest="departments",
            help="শুধু এই ডিপার্টমেন্ট id (একাধিকবার দেওয়া যায়)",
        )

    def handle(self, *args, departments=None, **options):
        # GenericIPAddressField ফাঁকা মান NULL হিসেবে রাখে, তাই isnull চেকই যথেষ্ট
        qs = (Department.objects.select_related("company")
              .exclude(device_ip__isnull=True)
              .exclude(device_port__isnull=True))
        if departments:
            qs = qs.filter(id__in=departments)

        devices = [{"ip": d.device_ip, "port": d.device_port, "department": d} for d in qs]
        if not devices:
            self.stdout.write(self.style.WARNING("No department has a device IP/Port set."))
            return

        failed = 0
        for res in import_attendance(devices):
            line = f"{res['department']}: {res['message']}"
            if res["status"] == "success":
                self.stdout.write(self.style.SUCCESS(line))
            else:
                failed += 1
                self.stderr.write(self.style.ERROR(line))

        if failed:
            # Task Scheduler এ ব্যর্থতা ধরা যায় এমন exit code
            raise CommandError(f"{failed} of {len(devices)} device(s) failed to sync.")
