from django.core.management.base import BaseCommand, CommandError

from attendance_app.models import Device
from attendance_app.devices.service import DeviceActionError, sync_device


class Command(BaseCommand):
    help = ("LAN হাজিরা মেশিন (ZKTeco/eSSL, Hikvision) থেকে হাজিরা সিঙ্ক করে — সব active LAN মেশিন, "
            "অথবা --device / --department দিয়ে নির্দিষ্টগুলো। Cloud push মেশিন নিজেই ডাটা পাঠায়, তাদের সিঙ্ক লাগে না।")

    def add_arguments(self, parser):
        parser.add_argument("--device", type=int, action="append", dest="devices", help="শুধু এই device id (একাধিকবার দেওয়া যায়)")
        parser.add_argument("--department", type=int, action="append", dest="departments",
                            help="শুধু এই ডিপার্টমেন্ট id এর মেশিন (একাধিকবার দেওয়া যায়)")
        parser.add_argument("--full", action="store_true", help="মেশিনের সব পুরনো ডাটাও আবার পড়ো")

    def handle(self, *args, devices=None, departments=None, full=False, **options):
        qs = (Device.objects.select_related("company", "department", "department__branch")
              .filter(is_active=True, protocol__in=Device.PULL_PROTOCOLS)
              .exclude(ip__isnull=True))
        if devices:
            qs = qs.filter(id__in=devices)
        if departments:
            qs = qs.filter(department_id__in=departments)

        targets = list(qs)
        if not targets:
            self.stdout.write(self.style.WARNING("No active LAN device found (Devices পেজে মেশিন যোগ করুন)."))
            return

        failed = 0
        for device in targets:
            try:
                res = sync_device(device, full=full)
                self.stdout.write(self.style.SUCCESS(f"{device.name}: {res['message']}"))
            except DeviceActionError as e:
                failed += 1
                self.stderr.write(self.style.ERROR(f"{device.name}: {e}"))

        if failed:
            # Task Scheduler এ ব্যর্থতা ধরা যায় এমন exit code
            raise CommandError(f"{failed} of {len(targets)} device(s) failed to sync.")
